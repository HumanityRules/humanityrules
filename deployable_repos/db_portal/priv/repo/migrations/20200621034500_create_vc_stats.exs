defmodule DbPortal.Repo.Migrations.CreateVcStats do
  use Ecto.Migration

  def change do
    create table(:vc_stats) do
      add :stat, :string, size: 32, null: false
      add :description, :string, size: 256
    end

    create unique_index(:vc_stats, [:stat ])
  end
end
