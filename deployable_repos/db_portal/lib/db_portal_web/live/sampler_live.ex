defmodule DbPortalWeb.SamplerLive do
  use DbPortalWeb, :live_view

  alias DbPortal.DynamicSampling.SamplerManager

  @impl true
  def mount(_params, _session, socket) do
    IO.puts "****** MOUNT *****"

    clusters = DbPortal.Repo.clusters("prod")

    socket =
      socket
      |> assign(:clusters, clusters)
      |> assign(:enabled_clusters, SamplerManager.enabled_clusters())

    {:ok, socket}
  end

  @impl true
  def handle_event("toggle_enabled", %{"cluster" => cluster_name}=parms, socket) do
    desired_sampling_state = !Map.get(parms, "currently_enabled")
    desired_change_text = if desired_sampling_state, do: "enable", else: "disable"
    case SamplerManager.change_sampling(cluster_name, desired_sampling_state) do
      :ok ->
        {:noreply, assign(socket, :enabled_clusters, SamplerManager.enabled_clusters())}
      {:error, e} -> {:noreply,
        assign(socket, enabled_clusters: SamplerManager.enabled_clusters())
        |> put_flash(:error, "Unable to #{desired_change_text} sampling for #{cluster_name}: #{e}")
      }
    end
  end

  #
  # for the template
  #

  defp schemas(cluster) do
    cluster.db_schemas
    |> Enum.map_join(", ", & &1.name)
  end

  attr :name, :string, required: true
  attr :schemas, :string, required: true
  attr :enabled, :string, required: true
  def cluster(assigns) do
    button_color = if assigns.enabled, do: "bg-ch-blue", else: "bg-gray-200"
    span_translation = if assigns.enabled, do: "translate-x-5", else: "translate-x-0"

    assigns = assign(assigns, button_color: button_color, span_translation: span_translation)
    ~H"""
    <li class="flex items-center justify-between gap-x-6 py-1">
      <div class="flex min-w-0 gap-x-4">
        <div class="min-w-0 flex-auto">
          <p class="text-sm font-semibold leading-6 text-gray-900"><%= @name %></p>
          <p class="mt-1 truncate text-xs leading-5 text-gray-500"><%= @schemas %></p>
        </div>
      </div>

      <!-- Enabled: "bg-indigo-600", Not Enabled: "bg-gray-200" -->
      <button phx-click="toggle_enabled" phx-value-cluster={@name} phx-value-currently_enabled={@enabled} type="button" class={"#{@button_color} relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:ring-offset-2"} role="switch" aria-checked="false">
        <span class="sr-only">Use setting</span>
        <!-- Enabled: "translate-x-5", Not Enabled: "translate-x-0" -->
        <span aria-hidden="true" class={"#{@span_translation} pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out"}></span>
      </button>
    </li>
    """
  end
end
