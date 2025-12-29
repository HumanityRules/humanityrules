defmodule DbPortalWeb.AdminLive do
  use DbPortalWeb, :live_view

  alias __MODULE__

  require Logger
  require Ecto.Query

  alias DbPortal.Repo

  use Ecto.Schema

  def nav_selected(), do: "Admin"

  @primary_key false
  embedded_schema do
    field :clusters, {:map, :boolean}, default: %{}
  end

  @impl true
  def mount(_params, _session, socket) do
    IO.puts "****** MOUNT *****"

    env = "prod"
    cluster_map = DbPortal.DbMetadata.clusters_by_name(env, false)
    clusters = Map.keys(cluster_map) |> Enum.sort
    changeset = changeset(%AdminLive{}, cluster_map |> Map.new(fn {name,c}-> {name, c.disabled} end) )
    socket =
      socket
      |> assign(:clusters, clusters)
      |> assign(:cluster_map, cluster_map)
      |> assign(:env, env)
      |> assign(:changeset, changeset)

    {:ok, socket}
  end

  def changeset(data, clusters) do
    data
    |> Ecto.Changeset.cast(%{clusters: clusters}, [:clusters])
  end

  def click_changeset(changeset, cluster) do
    cluster_map = Ecto.Changeset.get_field(changeset, :clusters)
    clusters = Map.update(cluster_map, cluster, true, &Kernel.!/1)
    Ecto.Changeset.change(changeset, %{clusters: clusters})
    |> validate_clusters_not_empty()
  end

  def validate_clusters_not_empty(changeset) do
    Ecto.Changeset.validate_change(
      changeset,
      :clusters,
      fn :clusters, clusters ->
        if Enum.any?(clusters, fn({_,val}) -> val==true end) do
          []
        else
          [clusters: "cannot be empty"]
        end
      end)
  end

  @impl true
  def handle_event("cluster_click", %{"cluster"=> cluster }=_p, socket) do
    cluster_struct = Map.get(socket.assigns.cluster_map, cluster)
    disabled_changeset = socket.assigns.changeset
    new_disabled_changeset = click_changeset(disabled_changeset, cluster)

    update_cluster_in_db(cluster_struct, !Ecto.Changeset.get_field(new_disabled_changeset, :clusters)[cluster])

    {:noreply, assign(socket, :changeset, new_disabled_changeset)}

  end

  def update_cluster_in_db(cluster_struct, enabled?) do
    Ecto.Changeset.change(cluster_struct, disabled: !enabled?)
    |> Repo.update()
  end
end
