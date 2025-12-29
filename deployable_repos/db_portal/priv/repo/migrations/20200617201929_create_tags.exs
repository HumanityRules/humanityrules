defmodule DbPortal.Repo.Migrations.CreateTags do
  use Ecto.Migration

  def change do
    create table(:tags) do
      add :key, :string, size: 32, null: false
      add :value, :string, size: 256, null: false
    end

    create unique_index(:tags, [:key, :value])
  end
end
