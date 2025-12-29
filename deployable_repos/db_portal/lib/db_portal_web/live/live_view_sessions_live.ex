defmodule DbPortalWeb.LiveViewSessionsLive do
  use Phoenix.LiveDashboard.PageBuilder

  defstruct root_pid: nil, view: nil, email: nil

  @impl true
  def menu_link(_, _) do
    {:ok, "LiveView Sessions"}
  end

  @impl true
  def render(assigns) do

    ~H"""
    <div>
      <h1>Active LiveView Sessions</h1>
      <.live_table id="lv-table" dom_id="lv-table" page={@page} title="Running LiveViews"
        row_fetcher={&fetch_sockets/2} rows_name="LiveViews">
        <:col field={:email} header="Email" sortable={:asc} />
        <:col field={:view} header="View" />
        <:col field={:root_pid} header="PID" />
      </.live_table>
    </div>
    """
  end


  defp fetch_sockets(_params, _node) do
    # Logic to fetch process information from system,
    #  filtering/sorting based on params (search, sort_by, etc.)
    liveviews = Periscope.all_sockets()
      |> Enum.map(fn {_idx, lv} ->
        email = Map.get(lv.assigns, :email, "")
        %{root_pid: "" <> inspect(lv.root_pid),
          view: lv.view,
          email: email}
      end)
    {liveviews, length(liveviews)}
  end
end
