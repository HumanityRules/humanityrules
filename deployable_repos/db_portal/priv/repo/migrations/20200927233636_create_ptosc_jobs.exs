defmodule DbPortal.Repo.Migrations.CreatePtoscJobs do
  use Ecto.Migration

  def change do
    create table(:ptosc_jobs) do
      add :table, :string, size: 128, null: false
      add :schema_name, :string, size: 48, null: false
      add :host, :string, size: 192, null: false
      add :user, :string, size: 128, null: false
      add :ticket, :string, null: false
      add :pausefile, :string, size: 128, null: false
      add :alter, :string, size: 1024, null: false
      add :created_at, :naive_datetime, null: false
    end
  end
end
