defmodule DbPortal.Repo.Migrations.CreateDbSchemas do
  use Ecto.Migration

  def change do
    create table(:db_schemas) do
      add :name, :string, limit: 256
      add :cluster_id, references(:clusters)
      timestamps()
    end

    create unique_index(:db_schemas, [:name, :cluster_id])

  end
end
