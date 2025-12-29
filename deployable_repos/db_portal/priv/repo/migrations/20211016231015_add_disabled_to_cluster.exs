defmodule DbPortal.Repo.Migrations.AddDisabledToCluster do
  use Ecto.Migration

  def change do

    alter table("clusters") do
      add :disabled, :boolean, default: false
    end

  end
end
