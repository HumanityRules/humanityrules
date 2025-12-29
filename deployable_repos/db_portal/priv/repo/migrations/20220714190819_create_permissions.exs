defmodule DbPortal.Repo.Migrations.CreatePermissions do
  use Ecto.Migration

  def change do
    create table("permissions") do
      add :email, :string, size: 100, null: false
      add :page, :string, size: 200, null: false
      add :deleted, :boolean, null: false, default: false
      add :tickets, :string, size: 200, null: false

      add :schema_id, references(:db_schemas)
      timestamps()
    end
    create unique_index :permissions, [:email, :page, :schema_id]



  end
end
