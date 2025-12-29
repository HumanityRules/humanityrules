defmodule DbPortal.Repo.Migrations.CreateDigests do
  use Ecto.Migration

  def change do
    create table(:digests, primary_key: false) do
      add :digest, :string, size: 16, primary_key: true
      add :mysql_digest, :string, size: 32
      add :query_example, :text, null: false
      add :digest_text, :string, size: 4096, null: false
      add :first_seen_at, :utc_datetime, null: false
    end
  end
end
