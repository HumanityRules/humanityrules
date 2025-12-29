defmodule DbPortal.Repo.Migrations.CreateBreakGlassAccounts do
  use Ecto.Migration

  def change do
    create table(:break_glass_accounts) do
      add :user_email, :string, size: 100, null: false
      add :db_user_name, :string, size: 100, null: false
      add :schema_name, :string, size: 100, null: false
      add :cluster_endpoint, :string, size: 255, null: false
      add :requested_at, :utc_datetime_usec, null: false
      add :expires_at, :utc_datetime_usec, null: false
      add :removed_at, :utc_datetime_usec, null: true, default: nil
    end
  end
end
