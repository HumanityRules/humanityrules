defmodule DbPortalWeb.PageLive do
  use DbPortalWeb, :live_view

  require Logger
  alias DbPortal.Analytics

 def nav_selected(), do: "Load Graphs"

  @impl true
  def mount(_params, _session, socket) do

    #data = Jason.encode!(@temp_data)
    time_data = Analytics.get_data("time_us") |> Jason.encode!
    tput_data = Analytics.get_data("tput") |> Jason.encode!
    rows_ex_data = Analytics.get_data("rows_examined.tput") |> Jason.encode!

    bundle_raw_time_data = Analytics.get_data("bundle.time_us")
    bundle_time_data = bundle_raw_time_data |> Jason.encode!
    bundle_tput_data = Analytics.get_data("bundle.tput") |> Jason.encode!

    # bundles = bundle_raw_time_data
    #           |> Enum.map(fn %{name: name}->{name,true} end)
    #           |> Map.new
    bundles = get_tag_values("symfony_entrypoint")
    drilldown_tag_values = []

    {:ok, assign(socket,
      time_data: time_data,
      tput_data: tput_data,
      rows_ex_data: rows_ex_data,
      bundles: bundles,
      drilldown_tag_values: drilldown_tag_values,
      full_bundle_time_data: bundle_time_data,
      full_bundle_tput_data: bundle_tput_data,
      bundle_time_data: bundle_time_data,
      bundle_tput_data: bundle_tput_data,
      drilldown_time_data: "[]",
      drilldown_tput_data: "[]",
      key: nil
    )}
  end

  @impl true
  def handle_event("toggle_bundle", %{"id" => bundle}, socket) do
    bundles = socket.assigns.bundles
              |> Map.update!(bundle, fn state-> !state end)
    bundle_time_data = socket.assigns.full_bundle_time_data
                       |> Jason.decode!
                       |> Enum.filter(fn %{"name"=> name}=_data_series -> Map.get(bundles, name) end)
                       |> Jason.encode!
    bundle_tput_data = socket.assigns.full_bundle_tput_data
                       |> Jason.decode!
                       |> Enum.filter(fn %{"name"=> name}=_data_series -> Map.get(bundles, name) end)
                       |> Jason.encode!
    {:noreply, assign(socket, bundles: bundles, bundle_time_data: bundle_time_data, bundle_tput_data: bundle_tput_data
    )}
  end

  @impl true
  def handle_event("select_all_bundles", _, socket) do
    bundles = socket.assigns.bundles
              |> Enum.map(fn({key,_val})->{key, true} end)
              |> Map.new

    bundle_time_data = socket.assigns.full_bundle_time_data
                       |> Jason.decode!
                       |> Enum.filter(fn %{"name"=> name}=_data_series -> Map.get(bundles, name) end)
                       |> Jason.encode!
    bundle_tput_data = socket.assigns.full_bundle_tput_data
                       |> Jason.decode!
                       |> Enum.filter(fn %{"name"=> name}=_data_series -> Map.get(bundles, name) end)
                       |> Jason.encode!
    {:noreply, assign(socket, bundles: bundles, bundle_time_data: bundle_time_data, bundle_tput_data: bundle_tput_data
    )}
  end

  @impl true
  def handle_event("clear_all_bundles", _, socket) do
    bundles = socket.assigns.bundles
              |> Enum.map(fn({key,_val})->{key, false} end)
              |> Map.new

    bundle_time_data = socket.assigns.full_bundle_time_data
                       |> Jason.decode!
                       |> Enum.filter(fn %{"name"=> name}=_data_series -> Map.get(bundles, name) end)
                       |> Jason.encode!
    bundle_tput_data = socket.assigns.full_bundle_tput_data
                       |> Jason.decode!
                       |> Enum.filter(fn %{"name"=> name}=_data_series -> Map.get(bundles, name) end)
                       |> Jason.encode!
    {:noreply, assign(socket, bundles: bundles, bundle_time_data: bundle_time_data, bundle_tput_data: bundle_tput_data
    )}
  end

  @impl true
  def handle_event("invert_bundles", _, socket) do
    bundles = socket.assigns.bundles
              |> Enum.map(fn({key,val})->{key, !val} end)
              |> Map.new

    bundle_time_data = socket.assigns.full_bundle_time_data
                       |> Jason.decode!
                       |> Enum.filter(fn %{"name"=> name}=_data_series -> Map.get(bundles, name) end)
                       |> Jason.encode!
    bundle_tput_data = socket.assigns.full_bundle_tput_data
                       |> Jason.decode!
                       |> Enum.filter(fn %{"name"=> name}=_data_series -> Map.get(bundles, name) end)
                       |> Jason.encode!
    {:noreply, assign(socket, bundles: bundles, bundle_time_data: bundle_time_data, bundle_tput_data: bundle_tput_data
    )}
  end

  @impl true
  def handle_event("drilldown_key", %{"value" => key}, socket) do
    Logger.info "drilldown_key key=#{key}"
    drilldown_tag_values = get_tag_values(key)
    Logger.info "drilldown_key drilldown_tag_values=#{inspect drilldown_tag_values}"
    {:noreply, assign(socket, drilldown_tag_values: drilldown_tag_values, key: key)}
  end

  @impl true
  def handle_event("drilldown", %{"key" => key, "value" => value}, socket) do
    Logger.info "drilldown key=#{key} value=#{value}"
    value = if "symfony_entrypoint"==key, do: "#{value}Bundle%", else: value

    drilldown_raw_time_data = Analytics.get_data("drilldown.time_us", [value: value, key: key])
    drilldown_time_data = drilldown_raw_time_data |> Jason.encode!


    drilldown_raw_tput_data = Analytics.get_data("drilldown.tput", [value: value, key: key])
    drilldown_tput_data = drilldown_raw_tput_data |> Jason.encode!

    {:noreply, assign(socket, drilldown_time_data: drilldown_time_data, drilldown_tput_data: drilldown_tput_data
    )}
  end

  defp get_tag_values("symfony_entrypoint"=key) do
    Analytics.get_tag_values(key, "LEFT(t.value, INSTR(t.value, 'Bundle')-1)")
    |> Enum.map(fn name->{name,true} end)
    |> Map.new
  end
  defp get_tag_values(key) do
    Analytics.get_tag_values(key)
    |> Enum.map(fn name->{name,true} end)
    |> Map.new
  end

end
