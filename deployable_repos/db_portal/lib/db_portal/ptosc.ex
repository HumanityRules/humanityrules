defmodule DbPortal.Ptosc do
  @moduledoc """
  Public interface to the Ptosc component.  Allows one to
  start ptosc jobs and receive updates on their progress (incl. completion)
  """
  require Logger
  require Ecto.Query

  alias DbPortal.Repo
  alias DbPortal.Ptosc.{Job, PtoscSupervisor, Tracker}

   # When this module is included in the "children" array passed to
   # Supervisor.start_link, it will find this method and use the information
   # it provides to start the Ptosc Supervisor
  def child_spec(opts) do
    %{
      id: PtoscSupervisor,
      start: {PtoscSupervisor, :start_link, [opts]},
      restart: :permanent
    }
  end

  def start_dryrun(nil) do
    {:error, "No command was given."}
  end
  def start_dryrun(cmd) when is_binary(cmd) do
    %Porcelain.Result{err: _err, status: status, out: out} =
      Porcelain.shell(cmd, [err: :out, in: (DbPortal.Secrets.value()|| "") <> "\n"])

    Logger.info "dry run completed status == #{status}"
    if status == 0 do
      {:ok, out}
    else
      {:error, out}
    end
  end

  @doc """
  start a ptosc job, publishing updates to its output on a pubsub channel,
  whose id will be returned as {:ok, channel_id}
  """
  def start_job(job_req) do
    # spawn process, registered and tracking output/status
    {:ok, job} = Map.from_struct(job_req)
                 |> Map.put(:created_at, NaiveDateTime.utc_now())
                 |> Job.new()
                 |> Repo.insert()

    job_with_statuses = Job.get_with_statuses(job.id)
    case PtoscSupervisor.start_job(job_with_statuses) do
      {:ok, tracker_pid} -> {:ok, tracker_pid, job_with_statuses}
      :ignore -> :ignore # should not occur
      {:error, error} -> {:error, error, job_with_statuses}
    end
  end

  @doc """
  return the ptosc command for the user to review before starting
  """
  def command(%{table: _table, alter: _alter, schema_name: _schema_name, host: _host}=job, action) do
    Job.command(job, action)
  end

  def get_recent_jobs() do
    Job.get_recent_jobs(10)
  end

  def current_status(job_id) when is_integer(job_id) do
    Job.get_with_statuses(job_id)
    |> current_status()
  end

  def current_status(%Job{}=job) do
    Tracker.current_status(job).status
  end

  def pause(job_id) do
    Job.pause(job_id)
  end
  def unpause(job_id) do
    Job.unpause(job_id)
  end
end
