defmodule DbPortal.DbMetadata.DbSchema do
  use Ecto.Schema
  import Ecto.Changeset

  schema "db_schemas" do
    field :name, :string
    timestamps()

    belongs_to :cluster, DbPortal.DbMetadata.Cluster
  end

  def changeset(db_schema, parms) do
    db_schema
    |> cast(parms, [:name])
    |> validate_required([:name])
  end

  def changeset_from_name(name) do
    %__MODULE__{}
    |> cast(%{name: name}, [:name])
    |> validate_required([:name])
  end

  def changeset_from_name(name, cluster_id) do
    %__MODULE__{}
    |> cast(%{name: name, cluster_id: cluster_id}, [:name, :cluster_id])
    |> validate_required([:name, :cluster_id])
  end
end
