defmodule DbPortalWeb.AthenaTools.CopyTableComponent do

  use DbPortalWeb, :live_component
  import Phoenix.HTML
  import Phoenix.HTML.Form
  use PhoenixHTMLHelpers

  require Logger

  @default_table %{table_name: "<default>", location: "s3://some_bucket/foobar",
    database_name: "foobardb"}

  @impl true
  def mount(socket) do
    {:ok, assign(socket, is_move: false)}
  end

  @impl true
  def update(assigns, socket) do
    {:ok, assign(socket, table_to_copy_dst: assigns.table_to_copy || @default_table,
      table_to_copy_src: assigns.table_to_copy|| @default_table,
      selected_tables: assigns.selected_tables) }
  end

  @impl true
  def render(assigns) do
    ~H"""
    <div>
    <.modal
      id="copy_or_move_tables"
      on_cancel={hide_modal("copy_or_move_tables")|>JS.push("on_close_button", target: "#{@myself}")}
      on_confirm={JS.push("on_submit_button", target: "#{@myself}") |> hide_modal("copy_or_move_tables")}
    >
      <:title>Set copy/move destination:</:title>
      <:cancel>Cancel</:cancel>
      <:confirm><%= move_or_copy_button_text(@is_move) %></:confirm>

      <form phx-submit="on_submit_button" phx-change="on_form_change" phx-target={ @myself }>
        <div class="bg-white grid grid-cols-3 gap-3 text-gray-500 w-full h-full items-center"
              style="grid-template-columns: fit-content(60px) auto 30px;">

          <div class="justify-self-end">
            <%= if length(@selected_tables) == 1, do: "Table", else: "Tables"%>:
          </div>
          <div class="max-h-24 px-3 py-2 border border-gray-500 overflow-y-auto">
            <ul>
              <%= for tbl <- @selected_tables do %>
                <li><%= tbl %></li>
              <% end %>
            </ul>
          </div>
          <div></div>

          <div class="justify-self-end">Database:</div>
          <input type="text" name="database_name" autocomplete="off" autocapitalize="off" spellcheck="false"
                  placeholder="Database Name" class="w-full h-8"
                  value={ @table_to_copy_dst.database_name }/>
          <button type="button" class="" phx-click="on_database_back" phx-target={ @myself }>
            <!-- heroicon reply -->
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6" />
            </svg>
          </button>

          <div class="justify-self-end">Table:</div>
          <input type="text" name="table_name" autocomplete="off" autocapitalize="off" spellcheck="false"
                placeholder="Table Name" class="w-full h-8"
                  value={ @table_to_copy_dst.table_name }/>
          <button type="button" phx-click="on_table_back" phx-target={ @myself } class="">
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6" />
            </svg>
          </button>

          <div class="justify-self-end">Location:</div>
          <input type="text" name="location" autocomplete="off" autocapitalize="off" spellcheck="false"
                  placeholder="Destination Location" class="w-full h-8"
                  value={ @table_to_copy_dst.location }/>
          <button type="button" class="" phx-click="on_location_back" phx-target={ @myself }>
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6" />
            </svg>
          </button>
        </div>
        <div class="flex justify-center pt-10 pb-4 w-3/4 m-auto">
        <button type="button" class={ (if @is_move, do: "bg-indigo-600", else: "bg-gray-200") <>
                                      " relative inline-flex flex-shrink-0 h-6 w-11
                                      border-2 border-transparent rounded-full cursor-pointer transition-colors ease-in-out duration-200
                                      focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500"}
                                      aria-pressed="false"
                                      phx-click={:on_move_toggle}
                                      phx-target={ @myself }>
            <span class="sr-only">Use setting</span>
            <span aria-hidden="true" class={ (if @is_move, do: "translate-x-5", else: "translate-x-0") <>
                                            " pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow transform ring-0 transition ease-in-out duration-200"}></span>
          </button>
          <span class="ml-3" id="annual-billing-label">
            <span class="text-sm font-medium text-gray-900">Switch copy/move</span>
          </span>
        </div>
      </form>
    </.modal>
  </div>
  """
  end

  @impl true
  def handle_event("on_move_toggle", _params, socket) do
    {:noreply, assign(socket, is_move: !socket.assigns.is_move)}
  end

  @impl true
  def handle_event("on_close_button", _params, socket) do
    send(self(), {:on_close_copy_table_button})
    {:noreply, socket}
  end

  @impl true
  def handle_event("on_database_back", _params, socket) do
    back = socket.assigns.table_to_copy_dst
      |> Map.put(:database_name, socket.assigns.table_to_copy_src.database_name)

    {:noreply, assign(socket, table_to_copy_dst: back)}
  end

  @impl true
  def handle_event("on_table_back", _params, socket) do
    back = socket.assigns.table_to_copy_dst
      |> Map.put(:table_name, socket.assigns.table_to_copy_src.table_name)

    {:noreply, assign(socket, table_to_copy_dst: back)}
  end

  @impl true
  def handle_event("on_location_back", _params, socket) do
    back = socket.assigns.table_to_copy_dst
      |> Map.put(:location, socket.assigns.table_to_copy_src.location)

    {:noreply, assign(socket, table_to_copy_dst: back)}
  end

  @impl true
  def handle_event("on_form_change", params, socket) do
    after_changes = socket.assigns.table_to_copy_dst
      |> Map.put(:database_name, params["database_name"])
      |> Map.put(:table_name, params["table_name"])
      |> Map.put(:location, params["location"])

    {:noreply, assign(socket, table_to_copy_dst: after_changes)}
  end

  @impl true
  def handle_event("on_submit_button", _params, socket) do
    send(self(),
      {:on_copy_table_button,
        %{:src => socket.assigns.table_to_copy_src,
          :dst => socket.assigns.table_to_copy_dst,
          :is_move => socket.assigns.is_move}
      })
    {:noreply, socket}
  end

  defp move_or_copy_button_text(true = _is_move), do: "Move"
  defp move_or_copy_button_text(false = _is_move), do: "Copy"

end
