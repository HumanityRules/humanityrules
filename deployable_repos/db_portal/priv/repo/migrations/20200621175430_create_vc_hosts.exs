defmodule DbPortal.Repo.Migrations.CreateVcHosts do
  use Ecto.Migration

  def change do
    create table(:vc_hosts) do
      add :name, :string, size: 128, null: false
    end

    create unique_index(:vc_hosts, [:name])
  end
end
