defmodule DbPortal.BreakGlass.AccountScrubber do
  use GenServer
  require Logger

  @minute_in_millis 60 * 1000
  @hour_in_millis 60 * @minute_in_millis

  def start_link(_args) do
    GenServer.start_link(__MODULE__, %{})
  end

  def init(state) do
    Logger.info("Starting BreakGlassAccountScrubber #{NaiveDateTime.utc_now}")
    schedule_next_scrub()
    {:ok, state}
  end

  def handle_info(:scrub, state) do
    do_scrubbing()
    schedule_next_scrub()
    {:noreply, state}
  end

  def do_scrubbing() do
    Logger.debug("BreakGlass.AccountScrubber scrubbing now")

    DbPortal.BreakGlass.Account.scrub_expired_accounts()
  end

  defp schedule_next_scrub() do
    Process.send_after(self(), :scrub, @hour_in_millis)
  end
end
