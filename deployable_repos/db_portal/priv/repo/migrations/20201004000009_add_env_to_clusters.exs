defmodule DbPortal.Repo.Migrations.AddEnvToClusters do
  use Ecto.Migration

  def change do

    alter table("clusters") do
      add :env, :string, size: 15, null: false, default: "prod"
    end

    alter table("clusters") do
      modify :env, :string, size: 15, null: false
    end

    create index("clusters", [:env])
  end
end
