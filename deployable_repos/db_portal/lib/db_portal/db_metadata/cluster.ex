defmodule DbPortal.DbMetadata.Cluster do
  use Ecto.Schema
  import Ecto.Changeset

  schema "clusters" do
    field :name, :string
    field :write_endpoint, :string
    field :read_endpoint, :string
    field :env, :string
    field :disabled, :boolean
    timestamps()

    has_many :db_schemas, DbPortal.DbMetadata.DbSchema, on_replace: :nilify
  end

  def from_aws(%__MODULE__{}=cluster, aws_cluster, env) do
    cluster
    |> cast(%{env: env}, [:env])
    |> cast(Map.from_struct(aws_cluster), [:name, :write_endpoint, :read_endpoint])
    |> validate_required([:name, :write_endpoint, :read_endpoint, :env])
    |> unique_constraint(:name)
  end

  def test_changeset(cluster, attrs) do
    cluster
    |> cast(attrs, [:name, :write_endpoint, :read_endpoint, :env])
    |> validate_required([:name, :write_endpoint, :read_endpoint, :env])
    |> unique_constraint(:name)
  end
end
