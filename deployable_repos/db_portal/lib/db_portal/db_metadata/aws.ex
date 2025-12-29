defmodule DbPortal.DbMetadata.Aws do
  alias DbPortal.MonitoredDbRepo

  defmodule Cluster do
    use Ecto.Schema

    embedded_schema do
      field :name, :string, source: :DBClusterIdentifier
      field :write_endpoint, :string, source: :Endpoint
      field :read_endpoint, :string, source: :ReaderEndpoint
    end
    @field_map %{"DBClusterIdentifier"=>"name",
      "Endpoint"=>"write_endpoint",
      "ReaderEndpoint"=>"read_endpoint",
    }
    defp remap(k), do: Map.get(@field_map,k, "ignore")

    def aws_to_cluster(params) do
      remapped_params = for {k,v} <- params, into: %{}, do: {remap(k), v}
      changeset =
        %__MODULE__{}
        |> Ecto.Changeset.cast(remapped_params, [:name, :write_endpoint, :read_endpoint])
#        |> validate_required

      if changeset.valid? do
        Ecto.Changeset.apply_changes(changeset)
      end
    end
  end

  # USE rs-prod-aurora-coursehero-1-cluster rather than using DBBI
  # matters for the indexes!
  @ignore_these_clusters ~w(prod-rc-1 rs-prod-rancher-aurora-cluster rs-prod-aurora-coursehero-2-dbbi-cluster dev-rancher-aurora-cluster)
  def clusters(env) do
    with {:ok, parsed_body, _} <- describe_db_clusters(env) do

      parsed_body
      |> Kernel.get_in(~w(DescribeDBClustersResponse DescribeDBClustersResult DBClusters DBCluster))
      |> Enum.map(&Cluster.aws_to_cluster/1)
      |> Enum.reject(&(Enum.member?(@ignore_these_clusters, &1.name)))
    end
  end

  @ignore_these_schemas ~w(information_schema performance_schema sys tmp dbadmin dbadmin2 mysql)
  def schema_map(env, clusters \\ nil) do
      clusters = clusters || clusters(env)
      clusters
      |> Enum.reduce(%{}, fn
        c, acc-> Map.put(acc, c.name,
            {c, MonitoredDbRepo.show_schemas(c.read_endpoint, @ignore_these_schemas)}
        )
      end)
  end

  def schemas(env, clusters \\ nil) do
    schema_map(env, clusters)
    |> Map.values()
    |> List.flatten()
  end

  def list_rds_instances(env, cluster_name) do
    opts = %{"Filters.Filter.1.Name" => "db-cluster-id", "Filters.Filter.1.Values.Value.1"=> cluster_name}
    instances= with {:ok, parsed_body, %{status_code: 200}} <- describe_db_instances(env, opts) do
      parsed_body
      |> Kernel.get_in(~w(DescribeDBInstancesResponse DescribeDBInstancesResult DBInstances DBInstance))
      |> Enum.map(&Kernel.get_in(&1, ~w(Endpoint Address)))
    end

    writer = with {:ok, parsed_body, _} <- describe_db_clusters(env, opts) do
      parsed_body
      |> Kernel.get_in(~w(DescribeDBClustersResponse DescribeDBClustersResult DBClusters DBCluster))
      |> get_in(~w(DBClusterMembers DBClusterMember))
      |> Enum.filter(&(Map.get(&1, "IsClusterWriter")=="true"))
      |> hd() # there should be only one writer
      |> Map.get("DBInstanceIdentifier")
    end

    Enum.sort_by(instances, fn i -> !String.starts_with?(i, writer) end)

  end

  def get_secret_value(key) do
    AWS.SecretsManager.get_secret_value(
      aws_client(),
      %{"SecretId"=>key}
    )
  end
  def describe_db_clusters(env, opts \\ %{}) do
    AWS.RDS.describe_db_clusters(
      aws_client(env),
      opts
    )
  end
  def describe_db_instances(env, opts \\ %{}) do
    AWS.RDS.describe_db_instances(
      aws_client(env),
      opts
    )
  end

  def aws_client(env \\ nil) do
    c=ExAws.Config.new(:s3, overrides_for_env(env))
    %AWS.Client{access_key_id: c.access_key_id,
      secret_access_key: c.secret_access_key,
      session_token: Map.get(c,:security_token),
      json_module: {c.json_codec,[]},
      # xml_module: {c.json_codec,[]}, # b/c we always set accept to json. REMOVED because it was causing issues with AWS.S3.list_objects()
      region: c.region,
      http_client: {c.http_client,[]}
    }
  end

  def overrides_for_env(env) do
    if env=="dev" do
      %{access_key_id: [
        {:awscli, "dev", 30},
      ],
      secret_access_key: [
        {:awscli, "dev", 30},
      ]}
    else
      %{}
    end

  end
end
