defmodule DbPortal.Repo.Migrations.CreateReadonlyQueryExecutions do
  use Ecto.Migration

  def change do
    create table(:readonly_query_executions) do
      add :user, :string, size: 100, null: false
      add :db_schema_id, references(:db_schemas), null: false
      add :query, :string, size: 1000, null: false
      add :rows_returned, :integer # null if error

      timestamps()
    end

  end
end
