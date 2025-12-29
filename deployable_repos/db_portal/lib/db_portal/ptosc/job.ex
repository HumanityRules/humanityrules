defmodule DbPortal.Ptosc.Job do

  # do not restart this process
  use GenServer, restart: :temporary
  require Logger
  require Ecto.Query

  alias __MODULE__
  alias DbPortal.{Ptosc, Ptosc.Tracker}
  alias Porcelain.Process, as: Proc
  alias Porcelain.Result
  alias DbPortal.Repo


  use Ecto.Schema
  import Ecto.Changeset

  schema "ptosc_jobs" do
    field :table, :string
    field :schema_name, :string
    field :host, :string
    field :user, :string
    field :ticket, :string
    field :alter, :string
    field :created_at, :naive_datetime
    field :chunk_time, :decimal

    has_many :statuses, Tracker, foreign_key: :ptosc_job_id
  end
  @required_fields ~w(table schema_name host user ticket alter created_at chunk_time)a

  def new(attrs) do
    %Job{}
    |> cast(attrs, @required_fields)
    |> validate_required(@required_fields)
    |> validate_number(:chunk_time, greater_than: 0.0, less_than: 3.0)
  end

  def get_with_statuses(job_id) do
    q = Ecto.Query.from job in Job,
      where: job.id == ^job_id,
      preload: [:statuses]

    DbPortal.Repo.one!(q)
  end

  def get_recent_jobs(count) do
    q = Ecto.Query.from job in Job,
        limit: ^count,
        order_by: [desc: job.id],
        preload: [:statuses]

    DbPortal.Repo.all q
  end

  def paused?(%Job{}=job), do: paused?(job.id)
  def paused?(job_id) do
    pausefile = Repo.get!(Job, job_id)
                |> pausefile_for_job()
    File.exists?(pausefile)
  end

  def pause(job_id) do
    GenServer.call(via_term(job_id), :pause)
  end
  def unpause(job_id) do
    GenServer.call(via_term(job_id), :unpause)
  end

  def pausefile_for_job(job) do
    id = Map.get(job, :id, "unknown")
    "/tmp/stop-#{id}-#{job.table}"
  end

  @doc """
  return the ptosc command for the user to review before starting
  """
  def command(job, action) do
    %{table: table, alter: alter, schema_name: schema_name, host: host, chunk_time: chunk_time}=job
    pausefile = pausefile_for_job(job)
    """
    ./bin/pt-online-schema-change \\
     --alter "#{alter}" \\
     --max-load Threads_connected=1100 \\
     --critical-load Threads_connected=3000 \\
     h='#{host}',D=#{schema_name},t=#{table},u=chdbadmin \\
     --ask-pass \\
     --nodrop-old-table \\
     --sleep 0.001 \\
     --chunk-time #{chunk_time} \\
     --pause-file #{pausefile} \\
     --recursion-method=none \\
     --#{action}
    """
  end


  def start_link(%Job{} = job) do
    GenServer.start_link(__MODULE__, job, name: via_term(job))
  end

  defp job_name(job_id) when is_number(job_id) or is_binary(job_id), do: "job--#{job_id}"
  defp job_name(job), do: "job--#{job.id}"
  defp via_term(job_id), do: {:via, Registry, {Registry.Ptosc, job_name(job_id)}}


  ######################

  #@command ["-c",  "for i in `seq 1 120`; do echo $i;sleep 1;done"]
  #@command "for i in `seq 1 120`; do echo $i;sleep 1;done"
  #@command "./bin/long_running.rb"

  @impl true
  def init(%Job{} = job) do
    Logger.info("Job.init #{inspect job}")
    # Process.flag(:trap_exit, true)

    #port = Port.open({:spawn, @command}, [:binary, :exit_status])
    #port = Port.open({:spawn_executable, "/bin/bash"}, [:binary, :exit_status, args: @command])
    proc = %Proc{pid: _pid} =
      Ptosc.command(job, "execute")
      #@command
      |> IO.inspect(label: "full run ptosc cmdline")
      |> Porcelain.spawn_shell(in: :receive, out: {:send, self()}, err: :out)

    Proc.send_input(proc, (DbPortal.Secrets.value()|| "") <> "\n")
    Tracker.job_starting( NaiveDateTime.utc_now(), job)

    {:ok, %{proc: proc, job: job} }
  end

  # This callback handles data incoming from the command's STDOUT
  #def handle_info({_port, {:data, text_line}}, %{job: job} = state) do
  @impl true
  def handle_info({_pid, :data, out_or_err, text_line}, %{job: job} = state) do
    Logger.info "Data #{out_or_err}: #{inspect text_line}"
    String.trim(text_line)
    |> Tracker.update_status(job)

    {:noreply, state }
  end

  # This callback tells us when the process exits
  #def handle_info({port, {:exit_status, status}}, %{job: job} = state) do
  @impl true
  def handle_info({_pid, :result, %Result{status: status}}, %{job: job} = state) do
    Logger.info "Port exit: :exit_status: #{status}"
    Tracker.job_terminated( status, job)

    {:stop, :normal, state}
  end

  @impl true
  def handle_info(msg, state) do
    Logger.error "Unhandled message: #{inspect msg}"
    {:noreply, state}
  end

  @impl true
  def handle_call(:pause, _from,  %{job: job}=state) do
    IO.inspect(job, label: "job being paused")
    pausefile =  pausefile_for_job(job)
    result = with :ok <- File.touch(pausefile) do
      Tracker.update_pause_status(job, true)
      :ok
    end
    {:reply, result, state }
  end

  @impl true
  def handle_call(:unpause,  _from, %{job: job} = state) do
    pausefile =  pausefile_for_job(job)
    result = with :ok <- File.rm(pausefile) do
      Tracker.update_pause_status(job, false)
      :ok
    end
    {:reply, result, state }
  end

  @impl true
  def handle_call(msg, from, state) do
    Logger.error "Unhandled call: #{inspect {msg, from, state}}"
    {:reply, nil, state}
  end

end
