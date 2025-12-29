defmodule DbPortal.SamplerScheduler do

  @moduledoc ~S"""
  Responsible for scheduling the sampling.  also (at least for now) responsible for
  scheduling the processing of the hourly metrics, which collects them from cache
  and writes them to the DB.
  """

  use GenServer
  require Logger
  alias DbPortal.{SampleStore, VividCortex}

  @minute_in_millis 60 * 1000
  @hour_in_seconds 60 * 60
  # don't do collection right on the hour, as we might miss some sampled in previous
  # hour
  @minute_offset_for_collection 10
  @host "read.coursehero.mysql.prod.coursehero.io"

  def start_link(_args) do
    GenServer.start_link(__MODULE__, %{})
  end

  def init(state) do
    Logger.info("Starting SamplerScheduler #{NaiveDateTime.utc_now}")
    schedule_sampling()
    schedule_collection()
    {:ok, state}
  end

  def handle_info(:sample, state) do
    Logger.debug("Starting sampler #{NaiveDateTime.utc_now}")
    do_sampling()
    schedule_sampling()
    {:noreply, state}
  end

  def handle_info({:collect, hour}, state) do
    Logger.debug("Starting collection for #{hour}")
    do_collection(hour)
    schedule_collection()
    {:noreply, state}
  end

  defp schedule_sampling() do
    Process.send_after(self(), :sample, @minute_in_millis)
  end

  def schedule_collection() do
    hour_to_collect = SampleStore.beginning_of_hour(NaiveDateTime.utc_now)
    Logger.debug("Scheduling collection #{next_collection_time_offset_millis()} now #{NaiveDateTime.utc_now}")
    Process.send_after(self(), {:collect, hour_to_collect}, next_collection_time_offset_millis())
  end

  defp do_sampling() do
    DbPortal.Sampler.sample @host
  end

  def do_collection(hour) do
    SampleStore.process_hourly_metrics(hour)
    VividCortex.process_hour(hour)
  end

  def next_collection_time_offset_millis() do
    now = NaiveDateTime.utc_now()
    one_hour_from_now = now |> NaiveDateTime.add( @hour_in_seconds)
    next_hour = %NaiveDateTime{ one_hour_from_now | minute: @minute_offset_for_collection, second: 20 }
    _offset_millis = 1000 * NaiveDateTime.diff(next_hour, now)
  end

end
