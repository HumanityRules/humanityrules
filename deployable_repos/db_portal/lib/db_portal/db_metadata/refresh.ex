defmodule DbPortal.DbMetadata.Refresh do
  alias DbPortal.DbMetadata.{Aws, Cluster, DbSchema}
  alias DbPortal.{Repo, MonitoredDbRepo}

  require Ecto.Query
  alias Ecto.Query

  def refresh(env) do
    portal_clusters_by_name = DbPortal.DbMetadata.clusters_by_name(env, false)
    aws_clusters = Aws.clusters(env)
    aws_clusters_by_name = aws_clusters
                           |> Enum.map(& {&1.name, &1} )
                           |> Map.new()
    aws_cluster_names = Map.keys(aws_clusters_by_name) |> MapSet.new()
    portal_cluster_names = Map.keys(portal_clusters_by_name) |> MapSet.new()

    portal_clusters_to_disable = MapSet.difference(portal_cluster_names, aws_cluster_names)
    disable_clusters(portal_clusters_to_disable, env)

    portal_clusters_to_possibly_update = MapSet.intersection(portal_cluster_names, aws_cluster_names)
    maybe_update_clusters(portal_clusters_to_possibly_update, portal_clusters_by_name, aws_clusters_by_name, env )

    clusters_to_insert = MapSet.difference(aws_cluster_names, portal_cluster_names)
    insert_clusters(clusters_to_insert, aws_clusters_by_name, env)
  end

  @ignore_these_schemas ~w(information_schema performance_schema sys tmp dbadmin dbadmin2 mysql)
  #  MonitoredDbRepo.show_schemas(c.read_endpoint, @ignore_these_schemas)
  defp disable_clusters(cluster_names, env) do
    cluster_name_list = MapSet.to_list(cluster_names)
    Query.from( c in Cluster, where: c.env == ^env and c.name in ^cluster_name_list)
    |> Repo.update_all( set: [disabled: true])
  end

  defp maybe_update_clusters(portal_clusters_to_possibly_update, portal_clusters_by_name, aws_clusters_by_name, env ) do
    portal_clusters_to_possibly_update
    |> Enum.map(& {Map.get(portal_clusters_by_name, &1), Map.get(aws_clusters_by_name, &1), env})
    |> Enum.map(&maybe_update_cluster/1)
  end

  defp maybe_update_cluster({portal_cluster, aws_cluster, env}) do
    Cluster.from_aws(portal_cluster, aws_cluster, env)
    |> Repo.update()

    current_schemas = MonitoredDbRepo.show_schemas(aws_cluster.read_endpoint, @ignore_these_schemas) || []

    existing_schemas = portal_cluster.db_schemas |> Enum.map(& &1.name) |> MapSet.new()
    removed_schemas = MapSet.difference(existing_schemas, MapSet.new(current_schemas)) |> MapSet.to_list()

    current_schemas
    |> Enum.reject(& MapSet.member?(existing_schemas, &1))
    |> Enum.map(& DbSchema.changeset_from_name(&1, portal_cluster.id))
    |> Enum.map(& Repo.insert(&1))

    if !Enum.empty?(removed_schemas) do
      Query.from(s in DbSchema, where: s.name in ^removed_schemas)
      |> Repo.update_all(set: [cluster_id: nil])
    end
  end

  defp insert_clusters(clusters_to_insert, aws_clusters_by_name, env) do
    clusters_to_insert
    |> Enum.map(& Map.get(aws_clusters_by_name, &1))
    |> Enum.map(&insert_cluster(&1, env))
  end

  defp insert_cluster(aws_cluster, env) do
    {:ok, inserted} = Cluster.from_aws(%Cluster{}, aws_cluster, env)
               |> Repo.insert() |> IO.inspect(label: "inserting cluster")

    schemas = MonitoredDbRepo.show_schemas(aws_cluster.read_endpoint, @ignore_these_schemas)
    if schemas != nil do
      schemas
      |> Enum.map(& DbSchema.changeset_from_name(&1, inserted.id))
      |> Enum.map(& Repo.insert(&1))
    else
      IO.puts("nil result for show_schemas on #{aws_cluster.name}")
    end
  end

end
