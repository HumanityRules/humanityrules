defmodule DbPortal.AthenaTools.AthenaJobServer do
  use GenServer
  require Logger

  alias DbPortal.AthenaTools.{AthenaJobWorker, AthenaGetTables}
  alias Phoenix.PubSub


  def start_link(_default_state) do
    GenServer.start_link(__MODULE__ , %{}, name: __MODULE__)
  end

  def init(state) do
    state = state |> Map.put(:worker_state, "idle")
                  |> reset_log()

    broadcast_update(state)

    {:ok, state}
  end

  def start_athena_job(athena_job) do
    GenServer.call(__MODULE__, {:new_athena_job, athena_job})
  end

  def operation_in_progress?() do
    GenServer.call(__MODULE__, {:operation_in_progress?})
  end

  def get_operation_log() do
    GenServer.call(__MODULE__, {:get_operation_log})
  end

  def get_athena_tables(database_name) do
    GenServer.call(__MODULE__, {:get_athena_tables, database_name})
  end



  def handle_call({:new_athena_job, _athena_job} = msg_param, _from, state) do
    if Map.get(state, :worker_state, "idle") do
      state = state |> Map.put(:worker_state, "start_requested")
                    |> reset_log()

      broadcast_update(state)
      GenServer.cast(AthenaJobWorker, msg_param)

      {:reply, :ok, state}
    else
      {:reply, :already_running, state}
    end
  end


  def handle_call({:get_athena_tables, database_name}, {return_pid, _call_reference}, state) do
    task_id = "get_athena_tables_from_#{inspect return_pid}"
    task_name = {:via, Registry, {Registry.AthenaAsyncOp, task_id}}

    old_task_from_registry = Registry.lookup(Registry.AthenaAsyncOp, task_id)

    case old_task_from_registry do
      [{pid, _value}] -> DynamicSupervisor.terminate_child(AthenaDynamicSupervisor, pid)
      [] -> nil
    end

    athena_job = %{
      :src_database => database_name,
      :return_pid => return_pid,
      :next_token => nil
    }

    {:ok, pid} = DynamicSupervisor.start_child(AthenaDynamicSupervisor, {AthenaGetTables, name: task_name})

    GenServer.cast(pid, {:get_athena_tables, athena_job})

    {:reply, :ok, state}
  end


  def handle_call({:operation_in_progress?}, _from, state) do
    {:reply, state.worker_state != "idle", state}
  end


  def handle_call({:get_operation_log}, _from, state) do
    {:reply, state.operation_log, state}
  end


  def handle_call({:athena_job_worker_start, athena_job}, _from, state) do
    if Map.get(state, :worker_state) != "start_requested" do
      raise "AthenaJobServer is in a wrong state!"
    end

    state = state |> Map.put(:worker_state, "running")
                  |> append_to_log(:info, "AthenaJobWorker started #{Jason.encode!(athena_job)}")

     broadcast_update(state)

    {:reply, :ok, state}
  end


  def handle_call({:athena_job_worker_end, athena_job}, _from, state) do
    if Map.get(state, :worker_state) != "running" do
      raise "AthenaJobServer is in a wrong state!"
    end

    state = state |> Map.put(:worker_state, "idle")
                  |> append_to_log(:info, "AthenaJobWorker ended #{Jason.encode!(athena_job)}")

    broadcast_update(state)

    {:reply, :ok, state}
  end


  def handle_call({:athena_job_worker_new_log_message, level, log_msg}, _from, state) do
    if Map.get(state, :worker_state) != "running" do
      raise "AthenaJobServer is in a wrong state!"
    end

    state = state |> append_to_log(level, log_msg)
    broadcast_update(state)

    {:reply, :ok, state}
  end


  defp broadcast_update(state) do
    PubSub.broadcast(DbPortal.PubSub, "athena_job_server",
                    {:athena_job_worker_update, state.worker_state, state.operation_log})
  end


  defp append_to_log(state, level, log_msg) do
    Logger.log(level, log_msg)
    log_msg = %{
      :timestamp => NaiveDateTime.local_now(),
      :level => level,
      :log_msg => log_msg
    }
    Map.put(state, :operation_log, state.operation_log ++ [log_msg])
  end


  defp reset_log(state) do
    Map.put(state, :operation_log, [])
  end

end
