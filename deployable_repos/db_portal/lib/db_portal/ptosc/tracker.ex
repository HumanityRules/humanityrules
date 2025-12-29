defmodule DbPortal.Ptosc.Tracker do
  @moduledoc """
  Maintains the state of a particular Ptosc job, including both
   * updating the DB when appropriate
   * tracking the incremental progress reported by the job

  Implemented as a GenServer paired with a Ptosc.Job that manages
  the actual work
  """

  # restart only after abnormal termination
  use GenServer, restart: :transient

  require Logger
  alias DbPortal.Ptosc.Job
  alias DbPortal.Repo
  alias __MODULE__

  use Ecto.Schema
  import Ecto.Changeset

  schema "ptosc_states" do
    field :status, :string
    field :extra, :string
    field :status_at, :naive_datetime

    belongs_to :ptosc_job, Job
  end
  @required_fields ~w(ptosc_job_id status status_at)a

  def starting_changeset(job, timestamp) do
    %Tracker{}
    |> cast(%{ptosc_job_id: job.id, status: "started", status_at: timestamp}, @required_fields)
    |> validate_required(@required_fields)
  end
  def ending_changeset(job, timestamp, reason) do
    trunc_reason = String.slice(reason, 0,255)
    %Tracker{}
    |> cast(%{ptosc_job_id: job.id, status: "ended", extra: trunc_reason, status_at: timestamp}, [:extra | @required_fields])
    |> validate_required(@required_fields)
  end
  def pausing_changeset(job, timestamp, pause?) do
    new_status = if pause?, do: "paused", else: "unpaused"

    %Tracker{}
    |> cast(%{ptosc_job_id: job.id, status: new_status, status_at: timestamp}, @required_fields)
    |> validate_required(@required_fields)
  end

  @timeout_after_exit 24*60*60*1000

  # Client
  def start_link(%Job{}=job) do
    # if the Tracker restarts, preload the statuses here as they are needed
    # note the restart will be with the job in the state before there are any statuses
    job = Job.get_with_statuses(job.id)

    # use a Registry so can register with strings as names so as not to exhaust
    # the atom space (which are never GC'd)
    GenServer.start_link(__MODULE__, job, name: {:via, Registry, {Registry.Ptosc, job_name(job)}})
  end

  def job_starting(timestamp, job) do
    {:starting, timestamp}
    |> notify_tracker(job)
  end

  def update_status(text, job) do
    {:status, text}
    |> notify_tracker(job)
  end

  def job_terminated(status, job) do
    {:exit, status}
    |> notify_tracker(job)
  end

  def update_pause_status(job, paused?) do
    {:pause_change, paused?}
    |> notify_tracker(job)
  end

  def current_status(%Job{}=job) do
    if !Enum.empty?(job.statuses) do
      # break ties from timestamp by id. `max_by` will use the first for ties
      reversed_statuses = job.statuses
        |> Enum.sort_by(fn s -> s.id end, :desc)
      Enum.max_by(reversed_statuses, fn s -> s.status_at end, NaiveDateTime)
    else
      %Tracker{status: :none_found}
    end
  end

  def updates(job_id) do
    try do
      GenServer.call(via_term(job_id), :get_updates)
    catch
      :exit, {:noproc, _}-> ["Tracker not available"]
    end
  end

  defp notify_tracker(msg, job) do
    [{pid, _value}] = Registry.lookup(Registry.Ptosc, job_name(job))
    send pid, msg
  end

  def joblist_topic(), do: "ptosc_joblist"
  def job_name(job_id) when is_number(job_id) or is_binary(job_id), do: "tracker-#{job_id}"
  def job_name(job), do: "tracker-#{job.id}"
  defp via_term(job_id), do: {:via, Registry, {Registry.Ptosc, job_name(job_id)}}

 # Server (callbacks)

  @impl true
  def init(job_defn) do
    state = %{job: job_defn, output: [], exit: nil}
    Process.send_after(self(), :check_paused, 15_000)
    {:ok, state}
  end

  @impl true
  def handle_info({:starting, timestamp}, %{job: job}=state) do
    starting_changeset(job, timestamp)
    |> Repo.insert

    job = Job.get_with_statuses(state.job.id)

    DbPortalWeb.Endpoint.broadcast_from(self(), joblist_topic(), "job_status_update", %{job: job})

    {:noreply, %{state | job: job}}
  end

  @impl true
  def handle_info({:status, text}, state) do
    Logger.info("Tracking update #{text}")

    local_now = NaiveDateTime.local_now() |> NaiveDateTime.to_iso8601()
    new_output = [local_now <> ":  " <> text | state.output]
    DbPortalWeb.Endpoint.broadcast_from(self(), job_name(state.job.id), "job_progress", %{output: new_output})

    {:noreply, %{state | output: new_output}}
  end

  @impl true
  def handle_info({:exit, status}, state) do
    #broadcast

    ending_changeset( state.job, NaiveDateTime.utc_now(), reason(status, state))
    |> Repo.insert

    job = Job.get_with_statuses(state.job.id)

    DbPortalWeb.Endpoint.broadcast_from(self(), joblist_topic(), "job_status_update", %{job: job})

    new_state = %{state | exit: status, job: job }
    Process.send_after(self(), :expire, @timeout_after_exit)
    {:noreply, new_state}
  end

  @impl true
  def handle_info({:pause_change, paused?}, %{job: job}=state) do
    pausing_changeset(job, NaiveDateTime.utc_now(), paused?)
    |> Repo.insert!

    job = Job.get_with_statuses(job.id)
    DbPortalWeb.Endpoint.broadcast_from(self(), joblist_topic(), "job_status_update", %{job: job})

    {:noreply, %{state | job: job}}
  end

  @impl true
  def handle_info(:check_paused, %{job: job}=state) do
    actually_paused = Job.paused?(job)
    known_status = current_status(job).status
    if known_status != "ended" do
      case {actually_paused, known_status=="paused"} do
        {true, false} -> update_pause_status(job, true)
        {false, true} -> update_pause_status(job, false)

        {false, false} -> nil
        {true, true} -> nil
      end
    end

    if Map.get(state, :exit, nil)==nil do
      Process.send_after(self(), :check_paused, 15_000)
    end

    {:noreply, state}
  end

  @impl true
  # we hold the state for a while for inspection after the job completes (see `handle_info({:exit, status}, state)`.  The    expire comes after that time.
  def handle_info(:expire, state) do
    Logger.warning("Tracker expire after ptosc exit: #{inspect state}")
    {:stop, :shutdown, state }
  end

  @impl true
  def handle_info(msg, state) do
    Logger.warning("Unrecognized message to Tracker #{inspect self()} #{inspect msg}")
    {:noreply, state }
  end

  @impl true
  def handle_call(:get_updates, _from, state) do
    {:reply, state.output, state}
  end

  @impl true
  def handle_call(msg, from, state) do
    Logger.warning("Unrecognized call to Tracker #{inspect self()} #{inspect {msg, from, state}}")
    {:noreply, state }
  end

  defp reason(0, _state), do: "success"
  defp reason(exit_code, state), do: "Failed (#{exit_code}): #{hd(state.output)}"
end
