defmodule DbPortal.DbMetadata do
  alias DbPortal.DbMetadata.{Aws, Cluster, Refresh}
  alias DbPortal.Repo

  require Ecto.Query
  require Pathex

  def schemas(env, permitted_schema_ids \\ :all) do
    Repo.schemas(env, permitted_schema_ids)
  end

  def clusters_by_name(env, exclude_disabled? ) do
    base_qry = Ecto.Query.from c in Cluster, where: c.env==^env
    qry = if exclude_disabled?, do: Ecto.Query.from(c in base_qry, where: c.disabled == false), else: base_qry

    Repo.all(qry)
    |> Repo.preload(:db_schemas)
    |> Enum.into(%{}, fn c-> {c.name, c} end)
  end


  def refresh(env) do
    Refresh.refresh(env)
  end

  def resource_id_for_writer(schema_id) do
     q = Ecto.Query.from s in DbPortal.DbMetadata.DbSchema,
      preload: [:cluster],
      where: s.id == ^schema_id
    schema = DbPortal.Repo.one(q)

    resource_id_for_writer_by_cluster_name(schema.cluster.name, schema.cluster.env)
  end

  defp resource_id_for_writer_by_cluster_name(cluster_name, env) do
    opts = %{"Filters.Filter.1.Name" => "db-cluster-id", "Filters.Filter.1.Values.Value.1"=> cluster_name} |> IO.inspect()

    path = Pathex.path "DescribeDBClustersResponse" / "DescribeDBClustersResult" / "DBClusters" / "DBCluster" / "DBClusterMembers" / "DBClusterMember"
    writer_path = Pathex.path "DescribeDBInstancesResponse" / "DescribeDBInstancesResult" / "DBInstances" / "DBInstance"

    with {:ok, resp, _body} <- Aws.describe_db_clusters(env, opts),
         {:ok, cluster_members}  <- Pathex.view(resp, path),
         %{"DBInstanceIdentifier"=>writer_instance} <- Enum.find(cluster_members, &(Map.get(&1, "IsClusterWriter") == "true")),
         writer_opts = %{"Filters.Filter.1.Name" => "db-instance-id", "Filters.Filter.1.Values.Value.1"=> writer_instance},
         {:ok, writer_resp, _body} <- Aws.describe_db_instances(env, writer_opts),
         {:ok, %{"DbiResourceId" => resource_id}} <- Pathex.view(writer_resp, writer_path)  do
      resource_id
    else
      :error -> {:error, :no_such_schema}
      {:error, %Mint.TransportError{reason: :nxdomain}} -> {:error, :bad_network}
      {:error, {:unexpected_response, _finch_response}} -> {:error, :expired_token} # likely
    end
  end

  def endpoints_and_schema(nil), do: {:error, "Must provide schema_id"}
  def endpoints_and_schema(schema_id) do
    q = Ecto.Query.from s in DbPortal.DbMetadata.DbSchema,
      join: c in assoc(s,:cluster),
      select: {c.read_endpoint, c.write_endpoint, s.name},
      where: s.id == ^schema_id

    DbPortal.Repo.one(q)
  end

  @doc """
    Given a schema id (db_schema.id) return a list of each db
    instance in the cluster, with the writer first
    """
  def instances(schema) do
    q = Ecto.Query.from s in DbPortal.DbMetadata.DbSchema,
      join: c in assoc(s,:cluster),
      select: {c.name, c.env},
      where: s.id == ^schema

    {cluster_name, env} = DbPortal.Repo.one(q)

    try do
      Aws.list_rds_instances(env, cluster_name)
    rescue
      _ -> []
    end

  end

  def get_initial_schema(env) do
    q = Ecto.Query.from s in DbPortal.DbMetadata.DbSchema,
        join: c in assoc(s,:cluster),
        limit: 1,
        order_by: [asc: s.name],
        where: c.env == ^env

    DbPortal.Repo.one(q)
  end


end
