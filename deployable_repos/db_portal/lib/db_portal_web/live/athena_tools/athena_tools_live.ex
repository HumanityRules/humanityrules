defmodule DbPortalWeb.AthenaTools.AthenaToolsLive do
  use DbPortalWeb, :live_view
  require Logger

  import DbPortalWeb.DropDownMenuComponent

  alias Phoenix.PubSub
  alias DbPortal.AthenaTools.{AthenaAws, AthenaJobServer}

  def nav_selected(), do: "Athena Tools"

  @max_tables_per_page 500

  @impl true
  def mount(_params, %{"user_auth" => user_auth_val} = _session, socket) do
    is_data_engr = DbPortal.Role.satisfies_need?(:data_engr, user_auth_val)
    socket = assign(socket,
             database_names: if(connected?(socket), do: AthenaAws.get_athena_catalog_db_names(), else: []),
             selected_db: nil,
             tables: [],
             unfiltered_tables: [],
             filter_by: "",
             selected_tables: [],
             loading_tables: false,
             total_pages: 1,
             page_number: 1,
             show_create_view_modal: false,
             create_view_in_progress: false,
             table_to_copy: nil,
             operation_view_state: (if AthenaJobServer.operation_in_progress?(), do: "in_progress", else: "no_operation"),
             operation_log:  AthenaJobServer.get_operation_log(),
             menu_entries: (if is_data_engr, do: [ "Select All", "Select None", "-", "Copy or Move Tables", "Move Tables to Recycle Bin"], else: [ "Select All", "Select None"]),
             is_data_engr: is_data_engr
            )

    PubSub.subscribe(DbPortal.PubSub, "athena_job_server")

    {:ok, socket}
  end

  @impl true
  def handle_params(%{"db_name"=> db_name}, _url, socket) do
    {:noreply, load_database_tables(socket, db_name)}
  end

  @impl true
  def handle_params(%{}, _url, socket) do
    {:noreply, socket}
  end

  def is_table_selected(selected_tables, table_name) do
    Enum.any?(selected_tables, &(&1 == table_name))
  end

  @impl true
  def handle_event("on_table_checkbox", %{"table-name" => table_name}, socket) do
    case is_table_selected(socket.assigns.selected_tables, table_name) do
      false -> {:noreply, assign(socket, selected_tables: socket.assigns.selected_tables ++ [table_name])}
      true  -> {:noreply, assign(socket, selected_tables: socket.assigns.selected_tables -- [table_name])}
    end
  end


  @impl true
  def handle_event("on_create_view_click", %{}, socket) do
    {:noreply, assign(socket, show_create_view_modal: true)}
  end


  @impl true
  def handle_event("on_close_create_view_click", %{}, socket) do
    {:noreply, assign(socket, show_create_view_modal: false)}
  end


  @impl true
  def handle_event("on_menu_entry_click", %{"menu-entry" => menu_entry}, socket) do
    #
    #        {:noreply, assign(socket, selected_tables: [])}
    #  end
    #
    #  def foobar("on_menu_entry_click", %{"menu-entry" => menu_entry}, socket) do
    IO.puts("Got menu event #{menu_entry}")
    case menu_entry do
      "Select All" ->
        {:noreply, assign(socket, selected_tables: Enum.map(socket.assigns.tables, &(&1.table_name)))}

      "Select None" ->
        {:noreply, assign(socket, selected_tables: [])}

    end
  end

  @impl true
  def handle_event("recycle_tables", _vals, socket) do
    res = AthenaJobServer.start_athena_job(%{
      :job_type => "MoveToRecycleBin",
      :src_database => socket.assigns.selected_db,
      :src_tables => socket.assigns.selected_tables
    })
    # TODO: Display errors
    case res do
      :ok -> Logger.info("AthenaJob started")
      :already_started -> Logger.error("AthenaJob already started")
    end
    {:noreply, socket}
  end

  @impl true
  def handle_event("copy_or_move_tables", _vals, socket) do
    # TODO: Display errors
    table_to_copy =
      case socket.assigns.selected_tables do
        [] -> nil
        [table_to_copy_name] ->
          Enum.find(socket.assigns.unfiltered_tables, nil, &(&1.table_name == table_to_copy_name))

        [_h1, _h2 | _tail]->
          %{
            :database_name => socket.assigns.selected_db,
            :table_name => "{table_name}",
            :location => "s3://some-bucket/some_database/{table_name}"
          }

      end
    {:noreply, assign(socket, table_to_copy: table_to_copy)}
  end

  @impl true
  def handle_event("delete_tables", _vals, socket) do
    res = AthenaJobServer.start_athena_job(%{
      :job_type => "DeletePermanently",
      :src_database => socket.assigns.selected_db,
      :src_tables => socket.assigns.selected_tables
    })
    # TODO: Display errors
    case res do
      :ok -> Logger.info("AthenaJob started")
      :already_started -> Logger.error("AthenaJob already started")
    end
    {:noreply, socket}
  end

  def handle_event("nav", %{"page" => page}, socket) do
    page_number = String.to_integer(page) |> min(socket.assigns.total_pages) |> max(0)

    socket = socket
      |> assign(page_number: page_number)
      |> filter(socket.assigns.filter_by)

    {:noreply, socket}
  end


  @impl true
  def handle_event("on_filter_by_change", %{"filter-by" => filter_by}, socket) do
    {:noreply, filter(socket, filter_by)}
  end


  @impl true
  def handle_event("on_close_operation_button_click", _params, socket) do
    socket = socket
      |> assign(operation_view_state: "no_operation")
      |> load_database_tables(socket.assigns.selected_db)

    {:noreply, socket}
  end


  @impl true
  def handle_info({:on_create_view_in_progress, _params}, socket) do
    {:noreply, assign(socket, create_view_in_progress: true)}
  end


  @impl true
  def handle_info({:on_create_view_submit_button, params}, socket) do
    Logger.info "on_create_view_submit_button #{params.database_name} #{params.view_name}"
    socket = case AthenaAws.create_or_replace_view!(params) do
      {:ok} ->
        put_flash(socket, :info, "Successfully created view #{params.database_name}.#{params.view_name}")
      {:error, error_msg} ->
        put_flash(socket, :error, error_msg)
    end

    {:noreply, assign(socket, create_view_in_progress: false)}
  end



  @impl true
  def handle_info({:new_athena_tables, tables, done}, socket) do
    socket = socket
      |> assign(unfiltered_tables: socket.assigns.unfiltered_tables ++ tables,
                loading_tables: !done)
      |> filter(socket.assigns.filter_by)

    {:noreply, socket}
  end


  @impl true
  def handle_info({:athena_job_worker_update, worker_state, operation_log}, socket) do
    new_operation_view_state =
      case worker_state do
        "idle" when socket.assigns.operation_view_state == "in_progress" -> "done"
        "running" -> "in_progress"
        _any_other -> "no_operation"
      end

    {:noreply, assign(socket, operation_view_state: new_operation_view_state,
                              operation_log: operation_log)}
  end


  @impl true
  def handle_info({:on_close_copy_table_button}, socket) do
    {:noreply, assign(socket, table_to_copy: nil)}
  end


  @impl true
  def handle_info({:on_copy_table_button, params}, socket) do
    job_type = if params.is_move, do: "MoveTables", else: "CopyTables"
    res = AthenaJobServer.start_athena_job(%{
        :job_type => job_type,
        :dst_table => params.dst,
        :selected_tables => get_selected_tables(socket)
      }
    )
    # TODO: Display errors
    case res do
      :ok -> Logger.info("AthenaJob started")
      :already_started -> Logger.error("AthenaJob already started")
    end

    {:noreply, assign(socket, table_to_copy: nil)}
  end


  defp load_database_tables(socket, database_name) do
    if database_name != nil do
      AthenaJobServer.get_athena_tables(database_name)
    end

    #    send(self(), {:new_athena_tables,[%{is_view: false, table_name: "foo", database_name: "csbx_rliebling", location: "s3://foo"}, %{is_view: false, table_name: "bar", database_name: "csbx_rliebling", location: "s3://foo"}] , true})
    assign(socket, selected_db: database_name,
                   tables: [],
                   unfiltered_tables: [],
                   selected_tables: [],
                   loading_tables: true,
                   delete_tables_allowed?: socket.assigns.is_data_engr and AthenaAws.permanently_delete_allowed?(database_name)
    )
  end


  defp filter(socket, filter_by) do
    filtered = if String.length(filter_by) > 0 do
      Enum.filter(socket.assigns.unfiltered_tables, &(String.contains?(&1.table_name, filter_by)))
    else
      socket.assigns.unfiltered_tables
    end

    total_pages = max(ceil(length(filtered) / @max_tables_per_page), 1)
    page_num = if socket.assigns.page_number > total_pages, do: total_pages, else: socket.assigns.page_number
    page_index_start = (page_num - 1) * @max_tables_per_page
    tables_for_current_page = Enum.slice(filtered, page_index_start, @max_tables_per_page)

    assign(socket, tables: tables_for_current_page,
                   total_pages: total_pages,
                   page_number: page_num,
                   filter_by: filter_by)
  end


  def get_paginator_range(page_number, total_pages) do
    low = max(page_number - 7, 1)
    high = min(low + 13, total_pages)
    Enum.to_list(low..high)
  end


  defp get_selected_tables(socket) do
    Enum.map(socket.assigns.selected_tables,
      fn table_name ->
        Enum.find(socket.assigns.unfiltered_tables, nil, &(&1.table_name == table_name))
      end
    )
  end

end
