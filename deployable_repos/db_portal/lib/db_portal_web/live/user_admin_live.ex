defmodule DbPortalWeb.UserAdminLive do
  #  use DbPortalWeb, :live_view
  #
  #  alias __MODULE__
  #
  #  require Logger
  #  require Ecto.Query
  #
  #  use Ecto.Schema
  #
  #  def nav_selected(), do: "User Admin"
  #
  #  @primary_key false
  #  embedded_schema do
  #    field :username, :string
  #    field :same_password, :boolean
  #    field :schema_name, :string
  #    field :grants, :string
  #    field :clusters, {:map, :boolean}, default: %{}
  #  end
  #  def changeset(changeset, attrs) do
  #    changeset
  #    |> Ecto.Changeset.cast(attrs, [:username, :same_password, :schema_name, :grants, :clusters])
  #    |> validate_clusters_not_empty()
  #  end
  #  def click_changeset(%{clusters: clusters} = data, cluster) do
  #    clusters = Map.update(clusters, cluster, true, &Kernel.!/1)
  #    Ecto.Changeset.change(data, %{clusters: clusters})
  #    |> validate_clusters_not_empty()
  #  end
  #
  #  def validate_clusters_not_empty(changeset) do
  #    IO.inspect changeset, label: "validate_clusters_not_empty"
  #    Ecto.Changeset.validate_change(
  #      changeset,
  #      :clusters,
  #      fn :clusters, clusters ->
  #        if Enum.any?(clusters, fn({_,val}) -> val==true end) do
  #          IO.inspect "got one"
  #          []
  #        else
  #          IO.inspect "none"
  #          [clusters: "cannot be empty"]
  #        end
  #      end)|> IO.inspect(label: "validate_clusters_not_empty complete")
  #  end
  #
  #  @impl true
  #  def mount(_parms, %{"user_auth" => _session} = user_auth, socket) do
  #    clusters = ~w(a b c)
  #    cluster_map = Map.new(clusters, fn c-> {c,false} end)
  #
  #    socket =
  #      socket
  #      |> assign(clusters: clusters)
  #      |> assign(changeset: changeset(%UserAdminLive{}, %{grants: "readonly", username: "rich", clusters: cluster_map}))
  #    {:ok, socket}
  #  end
  #
  #  @impl true
  #  def handle_event("cluster_click", parms, socket) do
  #    IO.inspect parms, label: "cluster_click"
  #    changeset = socket.assigns.changeset
  #                |>IO.inspect(label: "orig")
  #                |> Ecto.Changeset.apply_changes() # clear errors, valid? flag
  #                |> click_changeset(Map.get(parms, "cluster"))
  #                |> IO.inspect(label: "cluster_click assign")
  #
  #    {:noreply, assign(socket, changeset: changeset)}
  #  end
  #
  #  @impl true
  #  def handle_event("validate", %{"user_admin_live"=>parms}, socket) do
  #    IO.inspect parms, label: "validate parms"
  #    changeset =
  #      %UserAdminLive{}
  #      |> changeset(parms)
  #      |> Map.put(:action, :insert)
  #      |> IO.inspect()
  #
  #    {:noreply, assign(socket, changeset: changeset)}
  #  end
  #  @impl true
  #  def handle_event("execute", parms, socket) do
  #    IO.inspect parms, label: "EXECUTE"
  #    {:noreply, socket}
  #  end
  #
  #  def cluster(changeset, which_cluster) do
  #    Ecto.Changeset.get_field(changeset, :clusters, %{})
  #    |> Map.get(which_cluster,"")
  #  end
  #  def toggle_classes(true), do: ~s(bg-dodgerblue-100 border-dodgerblue-200 z-10)
  #  def toggle_classes(false), do: ~s(border-dodgerblue-100)
end
