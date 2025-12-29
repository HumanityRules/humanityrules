defmodule DbPortal.Ptosc.PtoscSupervisor do
  use DynamicSupervisor

  alias DbPortal.Ptosc.{Job, Tracker}

  def start_link(_init_args \\ []) do
    DynamicSupervisor.start_link(__MODULE__, :ok, name: __MODULE__)
  end

  def init(:ok) do
    DynamicSupervisor.init(strategy: :one_for_one)
  end

  def start_job(%Job{} = job) do
    # start the Job and the Tracker
    # the job should know the Tracker's pid or name to send msgs
    # if name, then can handle Tracker restarts more easily
    tracker = start_tracker(job) |> IO.inspect(label: "start of tracker")
    _worker = start_job_worker(job, tracker) |> IO.inspect(label: "start of job")
    tracker
  end

  # public to facilitate testing Tracker lifecycle
  def start_tracker(%Job{}=job) do
    DynamicSupervisor.start_child(__MODULE__, {Tracker, job})
  end
  defp start_job_worker(%Job{}=job, _tracker) do
    DynamicSupervisor.start_child(__MODULE__, {Job, job})
  end
end
