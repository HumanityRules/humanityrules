defmodule DbPortal.TrackerTest do
  use ExUnit.Case, async: true

  alias DbPortal.Ptosc.PtoscSupervisor
  alias Phoenix.Tracker
  alias DbPortal.Ptosc.{Job, Tracker}
  alias DbPortal.Repo

  setup do
    :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo)
  end

  test "current_status sorts properly" do
    datetimes_that_sort_wrong_as_structs = [~N[2020-02-29 10:00:01], ~N[2020-03-11 10:20:00], ~N[2020-03-15 10:00:00], ~N[2020-03-11 12:30:00]]

    job_statuses = datetimes_that_sort_wrong_as_structs
                   |> Enum.with_index()
                   |> Enum.map(fn {dt, i} -> %Tracker{status_at: dt, status: i} end)
    job = %Job{statuses: job_statuses}

    assert(Tracker.current_status(job).status == 2)
  end

  @doc """
    If the PTOSC job finishes in < 1second the started and ended statuses
    may have the same timestamp.  We want to be sure the one with the later
    `id` (in this case the "ended" status) will be selected as the current
    status
    """
  test "current_status handles ties properly" do
    datetime = ~N[2020-02-29 10:00:01]

    job_statuses = [1, 3, 2]
      |> Enum.map(fn i -> %Tracker{status_at: datetime,
        id: i,
        status: i}
      end)
    job = %Job{statuses: job_statuses}

    assert(Tracker.current_status(job).status == 3)
  end

  test "Tracker restarts properly" do
    {:ok, job} = %Job{
        table: "table",
        schema_name: "foo",
        host: "host",
        user: "user",
        ticket: "FOO-1",
        alter: "add column",
        created_at: NaiveDateTime.utc_now(:second),
        chunk_time: Decimal.from_float(0.70)
      }
      |> Repo.insert()

    # see https://hexdocs.pm/ecto_sql/Ecto.Adapters.SQL.Sandbox.html#module-collaborating-processes
    supervisor_pid = Process.whereis(DbPortal.Ptosc.PtoscSupervisor)
    Ecto.Adapters.SQL.Sandbox.allow(Repo, self(), supervisor_pid)

    PtoscSupervisor.start_tracker(job)

    Tracker.job_starting(NaiveDateTime.utc_now(:second), job)
    [{pid, _value}] = Registry.lookup(Registry.Ptosc, Tracker.job_name(job))
    Process.exit(pid, :kill)

    Process.sleep(50) # give time for restart
    Tracker.update_status("testing", job)

    updates = Tracker.updates(job)
    assert length(updates) == 1
    assert updates |> hd() |> String.ends_with?("testing")

  end
end
