defmodule DbPortal.Repo.Migrations.PtoscJobRemovePausefile do
  use Ecto.Migration

  def change do
    alter table(:ptosc_jobs) do
      remove :pausefile
    end
  end
end
