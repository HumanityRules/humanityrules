defmodule DbPortal.Repo.Migrations.CreateClusters do
  use Ecto.Migration

  def change do
    create table(:clusters) do
      add :name, :string, size: 256
      add :write_endpoint, :string, size: 256
      add :read_endpoint, :string, size: 256
      timestamps()
    end

    create unique_index(:clusters, [:name])

  end
end
