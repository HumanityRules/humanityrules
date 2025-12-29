defmodule DbPortal.AthenaTools.AthenaGetTables do
  use GenServer, restart: :temporary

  require Logger
  alias DbPortal.AthenaTools.AthenaAws


  def start_link(opts) do
    GenServer.start_link(__MODULE__, %{}, opts)
  end

  def init(state) do
    {:ok, state}
  end

  def handle_cast({:get_athena_tables, athena_job}, state) do
     {:ok, tables, next_token} = AthenaAws.get_athena_tables!(athena_job.src_database, athena_job.next_token)

    # Send new tables to caller (for instance, the liveprocess that requested them)
    send(athena_job.return_pid, {:new_athena_tables, tables, next_token == nil})

    # Continue fetching more tables with message to self
    if next_token != nil do
      GenServer.cast(self(), {:get_athena_tables, Map.put(athena_job, :next_token, next_token)})
      {:noreply, state}
    else
      {:stop, :normal, state}
    end
  end

end
