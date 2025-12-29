defmodule DbPortal.Repo.Migrations.Help do
  use Ecto.Migration

  def change do

    alter table("digests") do
      modify :mysql_digest, :string, size: 64
    end
  end
end
