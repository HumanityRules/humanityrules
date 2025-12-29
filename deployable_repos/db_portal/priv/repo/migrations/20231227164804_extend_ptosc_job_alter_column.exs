defmodule DbPortal.Repo.Migrations.ExtendPtoscJobAlterColumn do
  use Ecto.Migration

  def change do
    alter table("ptosc_jobs") do
      modify :alter, :string, size: 4096, null: false
    end
  end
end
