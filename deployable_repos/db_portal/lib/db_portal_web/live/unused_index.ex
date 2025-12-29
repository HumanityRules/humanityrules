defmodule DbPortalWeb.UnusedIndexLive do
  use DbPortalWeb, :live_view

  require Logger

  alias Phoenix.LiveView.AsyncResult

  def nav_selected(), do: "Unused Indexes"

  defmodule UnusedIndex do
    @fields [:table, :index, :read_only, :insert, :update, :write, :delete]
    defstruct @fields

    def to_struct(parms) do
      s =
        Enum.zip(@fields, parms)
        |> Map.new()
        |> convert_nums([:insert, :update, :write, :delete], 1_000_000_000)

      struct(__MODULE__, s)
    end

    def convert_nums(%{} = s, fields, denom) do
      fields
      |> Enum.reduce(s, fn f, st ->
        val =
          (st[f] / denom + 1)
          |> :math.log()
          |> Decimal.from_float()
          |> Decimal.round(2)

        Map.put(st, f, val)
      end)
    end
  end

  alias __MODULE__.UnusedIndex

  @page_size 20

  @impl true
  def mount(_params, _session, socket) do
    IO.puts("****** MOUNT *****")

    schema = 1

    socket =
      socket
      |> assign_async(
        [:all_unused, :disp_unused, :count],
        fn -> load_unused(schema, :write, :desc) end,
        reset: [:all_unused, :disp_unused]
      )
      |> assign(:page, 1)
      |> assign(:count, AsyncResult.ok(0))
      |> assign(:sort_by, :write)
      |> assign(:sort_dir, :desc)
      |> assign(:err, nil)
      |> assign(:env, "prod")
      |> assign(:schema, 1)
      |> assign(:index_tooltip, "")
      |> assign(:filter, "")

    {:ok, socket}
  end

  defp load_unused(schema, sort_by, sort_dir) do
    IO.inspect({schema, sort_by, sort_dir}, label: "load_unused starting")

    # To develop more easily
    # Process.sleep(500)
    # all =
    #   Enum.map(1..10, fn i ->
    #     %UnusedIndex{
    #       table: "table_#{i}",
    #       index: "index_#{i}",
    #       insert: 0,
    #       update: 0,
    #       write: 0,
    #       delete: 0
    #     }
    #   end)
    # {:ok, %{all_unused: all, disp_unused: paginate(all, 1, @page_size), count: length(all)}}

    case fetch_indexes(schema) do
      {:ok, raw} ->
        IO.puts("Raw has length #{length(raw)}")

        all =
          raw
          |> prepare_indexes(sort_by, sort_dir)

        {:ok, %{all_unused: all, disp_unused: paginate(all, 1, @page_size), count: length(all)}}

      {:error, _err} = e ->
        # put_flash(socket, :error, "Failed DB connection")
        IO.puts("**********************\nFAILED loading unused")
        e
    end
  end

  def update_socket(socket, schema) do
    sort_by = socket.assigns.sort_by
    sort_dir = socket.assigns.sort_dir

    socket
    |> assign_async(
      [:all_unused, :disp_unused, :count],
      fn -> load_unused(schema, sort_by, sort_dir) end,
      reset: [:all_unused, :disp_unused]
    )
    |> assign(:count, AsyncResult.ok(0))
    |> assign(:page, 1)
  end

  def sorter(by, dir) do
    fn collection ->
      Enum.sort_by(collection, fn s -> Map.get(s, by) end, dir)
    end
  end

  def prepare_indexes(raw_indexes, by, dir) do
    raw_indexes
    |> Enum.map(&UnusedIndex.to_struct/1)
    |> sorter(by, dir).()
  end

  def fetch_indexes(schema) do
    {_rd_endpoint, _wrt_endpoint, schema_name} = DbPortal.DbMetadata.endpoints_and_schema(schema)
    # do NOT propose removing UNIQUE constraints as
    # they may be vital even if mysql doesn't indicate they are used
    # Eg - they may still block inserts and also trigger ON DUPLICATE KEY...
    # @@innodb_read_only will be 1 on the readers, and 0 on the writer
    q = """
    SELECT object_name, index_name, @@innodb_read_only,
      SUM_TIMER_INSERT AS ins,
      SUM_TIMER_UPDATE AS upd,
      SUM_TIMER_WRITE AS wrt,
      SUM_TIMER_DELETE AS del
    FROM sys.schema_unused_indexes idx
    LEFT JOIN information_schema.table_constraints cons
      ON cons.constraint_schema = idx.object_schema
      AND cons.table_name = idx.object_name
      AND cons.constraint_name = idx.index_name
      AND cons.constraint_type = 'UNIQUE'
    LEFT JOIN performance_schema.table_io_waits_summary_by_table tbl
      USING (object_schema, object_name)
    WHERE idx.object_schema = '#{schema_name}'
      AND cons.constraint_schema IS NULL
    """

    result =
      DbPortal.DbMetadata.instances(schema)
      |> maybe_add_dbbi(schema)
      |> Task.async_stream(
        fn h ->
          DbPortal.MonitoredDbRepo.exec_ro_query(q, h, schema_name)
        end,
        timeout: 15_000
      )
      |> Enum.reduce_while(nil, &intersect_indexes/2)

    case result do
      nil ->
        {:error, "cluster not found"}

      {:error, _e} = err ->
        err

      {intersection, writer_rows} ->
        rows =
          Enum.filter(writer_rows, fn [tbl, ix | _rest] ->
            MapSet.member?(intersection, {tbl, ix})
          end)

        {:ok, rows}
    end
  end

  @dbbi "bi.coursehero.mysql.prod.coursehero.io"
  defp maybe_add_dbbi([] = _instances, _schema), do: []
  defp maybe_add_dbbi([writer | readers], "COURSE_HERO"), do: [writer, @dbbi | readers]
  defp maybe_add_dbbi([_writer | _readers] = instances, _schema), do: instances

  defp intersect_indexes({:ok, {:error, _e}} = err, _accum), do: {:halt, err}

  defp intersect_indexes({:ok, {:ok, %MyXQL.Result{rows: rows}}} = _qry_results, accum) do
    idx = set_of_idx(rows)

    is_writer =
      case rows do
        [r | _] ->
          IO.inspect(r)
          [_, _, read_only | _rest] = r
          0 == read_only

        [] ->
          false
      end

    if accum == nil do
      rows_val = if is_writer, do: rows
      {:cont, {idx, rows_val}}
    else
      {intersection, existing_rows_val} = accum
      rows_val = if is_writer, do: rows, else: existing_rows_val
      {:cont, {MapSet.intersection(intersection, idx), rows_val}}
    end
  end

  defp set_of_idx(rows) do
    rows
    |> Enum.map(fn [tbl, ix | _rest] -> {tbl, ix} end)
    |> MapSet.new()
  end

  @impl true
  def handle_event("sort_by", %{"value" => value}, socket) do
    socket =
      if Enum.member?(~w(table index insert update delete write), value) do
        v = String.to_atom(value)
        page = socket.assigns.page
        curr_dir = socket.assigns[:sort_dir]
        dir = if socket.assigns[:sort_by] == v, do: flip_dir(curr_dir), else: :desc

        all_unused =
          socket.assigns.all_unused.result
          |> sorter(v, dir).()

        display = paginate(all_unused, page, @page_size)

        socket
        |> assign(:sort_by, v)
        |> assign(:sort_dir, dir)
        |> assign(:disp_unused, AsyncResult.ok(display))
        |> assign(:all_unused, AsyncResult.ok(all_unused))
      else
        socket
      end

    {:noreply, socket}
  end

  @impl true
  def handle_event("tooltip", %{"idx" => table_index} = p, socket) do
    IO.inspect(p, label: "tooltip params")
    [table, index] = String.split(table_index, ":", parts: 2)
    false = Regex.match?(~r([;\\,'`"]), table)
    schema = socket.assigns.schema

    case tooltip_text(schema, table, index) do
      {:ok, create_table} -> {:noreply, assign(socket, :index_tooltip, create_table)}
      {:error, _e} -> {:noreply, assign(socket, :index_tooltip, "Not available")}
    end
  end

  @impl true
  def handle_event("filter", %{"filter" => filter} = p, socket) do
    IO.puts("Filter #{filter}")
    all = socket.assigns.all_unused.result

    regex =
      case filter |> Regex.escape() |> Regex.compile() do
        {:ok, r} -> r
        # if error just match everything
        _ -> ~r//
      end

    display = Enum.filter(all, fn idx -> String.match?(idx.table, regex) end)

    socket =
      socket
      |> assign(disp_unused: AsyncResult.ok(display), filter: filter)

    {:noreply, socket}
  end

  @impl true
  def handle_event("page", %{"page" => page} = p, socket) do
    all_unused = socket.assigns.all_unused.result

    page = String.to_integer(page)
    display = paginate(all_unused, page, @page_size)

    {:noreply,
     socket
     |> assign(:page, page)
     |> assign(:disp_unused, AsyncResult.ok(display))}
  end

  @impl true
  def handle_event("page", %{"adjust" => dir}, socket) do
    all_unused = socket.assigns.all_unused.result

    page = socket.assigns.page
    new_page = if dir == "right", do: page + 1, else: page - 1

    display = paginate(all_unused, new_page, @page_size)

    {:noreply,
     socket
     |> assign(:page, new_page)
     |> assign(:disp_unused, AsyncResult.ok(display))}
  end

  defp paginate(all, page, page_size) do
    display =
      all
      |> Enum.slice((page - 1) * page_size, page_size)

    display
  end

  defp tooltip_text(schema, table, _index) do
    DbPortal.MonitoredDbRepo.show_create_table(schema, table)
  end

  defp pagination_helper(count, page) do
    num_pages =
      ceil(count / @page_size)

    cond do
      num_pages <= 1 ->
        ""

      true ->
        make_page_links((page - 2)..(page + 2), num_pages, page)
    end
  end

  defp make_page_links(page_range, num_pages, page) do
    ellipses = %{left: !(2 in page_range), right: !((num_pages - 1) in page_range)}
    page_range = Enum.reject(page_range, &(&1 <= 1 || num_pages <= &1))
    assigns = %{num_pages: num_pages, ellipses: ellipses, page_range: page_range, page: page}

    ~H"""
    <span>
      <%= if @page > 1 do %>
        <a phx-click="page" phx-value-adjust="left" href="#">&lt-</a>
      <% end %>
      <a phx-click="page" phx-value-page="1" class={if 1==@page, do: "font-bold"} href="#">1</a>
      <%= if @ellipses[:left] do %>
        <a>...</a>
      <% end %>
      <%= for p<-@page_range do %>
          <a phx-click="page" phx-value-page={p} class={if p==@page, do: "font-bold"} href="#"><%= p %></a>
      <% end %>
      <%= if @ellipses[:right] do %>
        <span>...</span>
      <% end %>
      <a phx-click="page" phx-value-page={@num_pages} class={if @num_pages==@page, do: "font-bold"} href="#"><%= @num_pages%></a>
      <%= if @page < @num_pages do %>
        <a phx-click="page" phx-value-adjust="right" href="#">-&gt</a>
      <% end %>
    </span>
    """
  end

  defp flip_dir(:desc), do: :asc
  defp flip_dir(:asc), do: :desc

  @impl true
  def handle_info({:updated_env, %{env: env, schema: schema}}, socket) do
    socket =
      socket
      |> assign(:env, env)
      |> assign(:schema, schema)

    {:noreply, socket}
  end

  @impl true
  def handle_info({:updated_schema, %{schema: schema}}, socket) do
    IO.inspect(schema, label: "updated_schema in unused")

    socket = assign(socket, :schema, schema)
    socket = update_socket(socket, schema)

    {:noreply, socket}
  end
end
