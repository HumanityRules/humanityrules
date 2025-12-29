defmodule DbPortalWeb.AthenaTools.CreateViewComponent do

  use DbPortalWeb, :live_component
  import Phoenix.HTML
  import Phoenix.HTML.Form
  use PhoenixHTMLHelpers

  require Logger

  @impl true
  def mount(socket) do
    {:ok, assign(socket,
      view_name: nil,
      view_select_query: nil,
      view_name_error: false,
      view_select_query_error: false,
      create_view_result_message: false,
      disable_create_view_btn: true
    )}
  end


  @impl true
  def update(assigns, socket) do
    socket = assign(
      socket,
      selected_db: assigns.selected_db,
      create_view_in_progress: assigns.create_view_in_progress
    )
    {:ok, assign(socket, disable_create_view_btn: should_disable_create_view_btn?(socket))}
  end

  @impl true
  def handle_event("on_create_view_form_change", %{
    "view-name" => view_name,
    "select-query" => select_query
  }, socket) do
    Logger.info "on_create_view_form_change: #{view_name} #{select_query}"
    socket = assign(
      socket,
      view_name: view_name,
      view_name_error: view_name_has_error?(view_name),
      view_select_query: select_query,
      view_select_query_error: select_query_has_error?(select_query)
    )
    {:noreply, assign(socket, disable_create_view_btn: should_disable_create_view_btn?(socket))}
  end

  @impl true
  def handle_event("on_create_view_submit", %{
    "database-name" => database_name,
    "view-name" => view_name,
    "select-query" => select_query
  }, socket) do
    Logger.info "on_create_view_submit #{database_name} #{view_name} #{select_query}"
    send(self(), {:on_create_view_in_progress,%{}})
    send(self(),
      {:on_create_view_submit_button,
        %{:database_name => database_name,
          :view_name => view_name,
          :select_query => select_query}
      })
    {:noreply, socket}
  end

  def view_name_has_error?(view_name) do
    String.length(view_name) > 0 && (String.contains?(view_name, " ") or not String.match?(view_name, ~r/^[A-Z]/i))
  end

  def select_query_has_error?(select_query) do
    String.length(select_query) >= 6 and not String.starts_with?(String.upcase(select_query), "SELECT")
  end

  def should_disable_create_view_btn?(socket) do
    socket.assigns.create_view_in_progress
    or socket.assigns.selected_db == nil
    or socket.assigns.view_name == ""
    or socket.assigns.view_select_query == ""
    or socket.assigns.view_name_error
    or socket.assigns.view_select_query_error
  end
end
