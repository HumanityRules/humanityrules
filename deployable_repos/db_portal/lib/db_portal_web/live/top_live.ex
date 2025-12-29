defmodule DbPortalWeb.TopLive do
  use DbPortalWeb, :live_view

  require Logger

  def nav_selected(), do: "DB Top"

  @page_size 30

  @cols ["Latency", "%", "Fetch", "Insert", "Update", "Delete", "Table", "Read Latency", "Write Latency", "Fetch Latency", "Insert Latency", "Update Latency", "Delete Latency", "Read Count", "Write Count", "Fetch Count", "Insert Count", "Update Count", "Delete Count" ]
  @col_index @cols |>Enum.with_index()|>Map.new()
  @views %{
    "Latency"=>["Latency", "%", "Fetch", "Insert", "Update", "Delete", "Table"],
    "Write Latency"=>["Write Latency", "Insert Latency", "Update Latency", "Delete Latency", "Table"],
    "Write Counts"=>["Write Count", "Fetch Count", "Insert Count", "Update Count", "Delete Count", "Table"]
  }

  @impl true
  def mount(_params, _session, socket) do
    IO.puts "****** MOUNT *****"

    socket =
      socket
      |> assign(:env, "prod")
      |> assign(:schema, 1)
      |> assign(:rows, [ ~w(1 2 3 4 5), ~w(2 3 4 5 6)])
      |> assign(:count, 0)
      |> assign(:sort_by, "Latency")
      |> assign(:sort_dir, :desc)
      |> assign(:err, nil)
      |> assign(:views, ["Latency", "Write Latency", "Write Counts"])
      |> assign(:cols, @views["Latency"])
      |> assign(:current_view, "Latency")
      |> assign(:col_index, @col_index)
      |> assign(:show_absolute, true)

    {:ok, socket}
  end

  @impl true
  def handle_params(_params, _uri, socket) do

    {:noreply, fetch_and_update(socket)}
  end

  def fetch_and_update(socket) do
    case fetch_waits(socket.assigns.schema) do
      {:ok, rows, cols} -> update_socket(socket, {rows, cols})

      {:error, _err} ->
        put_flash(socket, :error, "Failed DB connection")
    end
  end


  def update_socket(socket, {rows, cols}) do
    sort_by = socket.assigns.sort_by
    sort_dir = socket.assigns.sort_dir
    show_absolute = socket.assigns.show_absolute

    {{rows, cols}, socket}
    |> maybe_save_baseline(show_absolute)
    |> maybe_adjust_to_baseline(show_absolute)
    |> prepare_totals(sort_by, sort_dir)
    |> update_assigns()
  end

  def maybe_save_baseline({{rows, _cols}=rc, socket}, false) do
    {rc, assign(socket,
      relative_rows: rows,
      relative_time: DateTime.utc_now()
    )}
  end

  def maybe_save_baseline({{rows, cols}, socket}, true) do
    {{rows, cols}, assign(socket,
      baseline: rows,
      baseline_time: DateTime.utc_now(),
      relative_rows: rows,
      relative_time: DateTime.utc_now()
    )}
  end

  def maybe_adjust_to_baseline({{_rows, _cols}, _socket}=r, true), do: r
  def maybe_adjust_to_baseline({{rows, cols}, socket}, false) do
    baseline = socket.assigns.baseline
    base_map = baseline
               |> Enum.reduce(%{}, fn [sch, name | rest], acc ->
                 Map.put(acc, {sch, name}, rest)
               end)
    rel_rows = Enum.map(rows, fn [sch, name | rest]=_row ->
      base_row = Map.get(base_map, {sch,name})
      if nil == base_row, do: IO.inspect({sch,name}, label: "not in basemap")
      rel_stats = Enum.zip(rest, base_row)
                  |> Enum.map(fn {now, then} -> now-then end)
      [sch, name | rel_stats]
    end)
    {{rel_rows, cols}, socket}
  end

  def sorter(by, dir) do
    col_index = @col_index[by]
    fn collection ->
      collection
      |> Enum.reject(&Enum.at(&1, col_index)=="Nan")
      |> Enum.sort_by(fn r-> Enum.at(r, col_index) end, dir)
    end
  end

  def fetch_waits(schema) do
    {_rd_endpoint, wrt_endpoint, schema_name} = DbPortal.DbMetadata.endpoints_and_schema(schema)
    q = """
        SELECT OBJECT_SCHEMA, OBJECT_NAME,
          COUNT_STAR, SUM_TIMER_WAIT,
          COUNT_READ, SUM_TIMER_READ,
          COUNT_WRITE, SUM_TIMER_WRITE,
          COUNT_FETCH, SUM_TIMER_FETCH,
          COUNT_INSERT, SUM_TIMER_INSERT,
          COUNT_UPDATE, SUM_TIMER_UPDATE,
          COUNT_DELETE, SUM_TIMER_DELETE
        FROM table_io_waits_summary_by_table
        WHERE object_schema = '#{schema_name}'
        """
    with {:ok, result} <- DbPortal.MonitoredDbRepo.exec_ro_query(q, wrt_endpoint, "performance_schema"),
         %MyXQL.Result{rows: rows, columns: cols} <- result
    do
      {:ok, rows, cols}
    else
      {:error, _e}=err -> err |> IO.inspect(label: "in fetch")
    end
  end

  def prepare_totals({{rows, _cols}, socket}, sort_by, sort_dir) do
    total_timer = rows
                  |> Enum.map(fn [_,_,_,sumTimerWait | _rest] -> sumTimerWait end)
                  |> Enum.sum()
    new_rows =
      rows
      |> Enum.map(&project_rows(&1,total_timer))
      |> sorter(sort_by, sort_dir).()
      |> Enum.take(@page_size)

    projected_cols = @cols
    {{new_rows, projected_cols}, socket}
  end

  def project_rows([ _schema, table,
    _c_tot, s_tot,
    c_rd, s_rd,
    c_wr, s_wr,
    c_fe, s_fe,
    c_in, s_in,
    c_up, s_up,
    c_de, s_de], total_timer) do

      calc= if s_tot==0 || total_timer == 0 do
        ["Nan", "Nan",  "Nan", "Nan", "Nan", "Nan", table]
      else
        [s_tot, s_tot/total_timer, s_fe/s_tot, s_in/s_tot, s_up/s_tot, s_de/s_tot]
      end
      calc ++ [ table, s_rd, s_wr, s_fe, s_in, s_up, s_de, c_rd, c_wr, c_fe, c_in, c_up, c_de]
  end

  def format_cell(d, col_hdg) when is_float(d) and col_hdg in ["%", "Fetch", "Insert", "Update", "Delete"] do
    (d
    |> Decimal.from_float()
    |> Decimal.mult(100)
    |> Decimal.round(2)
    |> Decimal.to_string()
    ) <> "%"
  end
  def format_cell(d, col_hdg) when col_hdg in ["Latency", "Read Latency", "Write Latency", "Fetch Latency", "Insert Latency", "Update Latency", "Delete Latency" ], do: picos_to_time(d)

  def format_cell(d, _col_hdg) when is_integer(d) and d>1_000_000_000_000, do: "#{Float.round(d/1_000_000_000_000, 3)} T"
  def format_cell(d, _col_hdg) when is_integer(d) and d>1_000_000_000, do: "#{Float.round(d/1_000_000_000, 3)} B"
  def format_cell(d, _col_hdg) when is_integer(d) and d>1_000_000, do: "#{Float.round(d/1_000_000, 3)} M"
  def format_cell(d, _col_hdg), do: d


  def update_assigns({{rows, _cols}, socket}) do
    socket
    |> assign(rows: rows)
  end

  @impl true
  def handle_event("toggle_relative", %{"abs_rel"=>"rel"}, socket) do
    socket = assign(socket, show_absolute: false)
    socket = fetch_and_update(socket)

    {:noreply, socket}
  end
  def handle_event("toggle_relative", %{"abs_rel"=>"abs"}, socket) do
    socket = assign(socket, show_absolute: true)
    socket = fetch_and_update(socket)

    {:noreply, socket}
  end
  def handle_event("refresh_relative", _, socket) do
    socket = fetch_and_update(socket)

    {:noreply, socket}
  end
  def handle_event("sort_by", %{"value"=>sort_col}, socket) do
    sort_dir = if socket.assigns.sort_by == sort_col do
      flip_dir(socket.assigns.sort_dir)
    else
      :desc
    end

    socket = socket
             |> assign(sort_by: sort_col, sort_dir: sort_dir )
             |> update_socket({socket.assigns.relative_rows, socket.assigns.cols})

    {:noreply, socket}
  end

  def handle_event("set_view", %{"view"=>view}, socket) do
    sort_by = @views[view] |> hd
    sort_dir = :desc
#    {{new_rows, _}, _} = {{socket.assigns.relative_rows, nil}, socket}
#                         |> maybe_adjust_to_baseline(socket.assigns.show_absolute)
#                         |> prepare_totals(sort_by, sort_dir)
    socket = socket
             |> assign(current_view: view, cols: @views[view],
               sort_by: sort_by, sort_dir: sort_dir )
             |> update_socket({socket.assigns.relative_rows, socket.assigns.cols})
    {:noreply, socket}
  end

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
    IO.inspect schema, label: "updated_schema in top"

    # force to absolute as we won't have a baseline to compare with for relative
    socket =
      socket
      |> assign(schema: schema, show_absolute: true)
    socket = fetch_and_update(socket)

    {:noreply, socket}
  end

  @picos_per_ns 1_000
  @picos_per_us 1_000 * @picos_per_ns
  @picos_per_ms 1_000 * @picos_per_us
  @picos_per_s 1_000 * @picos_per_ms
  @picos_per_m 60 * @picos_per_s
  @picos_per_hr 60*@picos_per_m
  def picos_to_time(0), do: ""
  def picos_to_time(picos) when picos>=@picos_per_hr do
    (picos
    |> Decimal.new
    |> Decimal.div(@picos_per_hr)
    |> Decimal.round(2)
    |> Decimal.to_string()
    ) <> " h"
  end
  def picos_to_time(picos) when picos>=@picos_per_m do
    mins = picos
           |> div(@picos_per_m)
           |> to_string()
    secs = picos
           |> rem(@picos_per_m)
           |> div(@picos_per_s)
           |> to_string()
    mins <> " m " <> secs <> " s"
  end
  def picos_to_time(picos) when picos>=@picos_per_s do
    (picos
    |> Decimal.new
    |> Decimal.div(@picos_per_s)
    |> Decimal.round(2)
    |> Decimal.to_string()
    ) <> " s"
  end
  def picos_to_time(picos) when picos>=@picos_per_ms do
    (picos
    |> Decimal.new
    |> Decimal.div(@picos_per_ms)
    |> Decimal.round(2)
    |> Decimal.to_string()
    ) <> " ms"
  end
  def picos_to_time(picos) when picos>=@picos_per_us do
    (picos
    |> Decimal.new
    |> Decimal.div(@picos_per_us)
    |> Decimal.round(2)
    |> Decimal.to_string()
    ) <> " us"
  end
  def picos_to_time(picos) when picos>=@picos_per_ns do
    (picos
    |> Decimal.new
    |> Decimal.div(@picos_per_ns)
    |> Decimal.round(2)
    |> Decimal.to_string()
    ) <> " ns"
  end
  def picos_to_time(picos) do
    (picos
    |> to_string) <> "ps"
  end

  defp flip_dir(:asc), do: :desc
  defp flip_dir(:desc), do: :asc

end
