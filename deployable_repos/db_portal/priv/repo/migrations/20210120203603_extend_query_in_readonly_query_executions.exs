defmodule DbPortal.Repo.Migrations.ExtendQueryInReadonlyQueryExecutions do
  use Ecto.Migration

  def change do
    alter table("readonly_query_executions") do
      modify :query, :text
    end
  end
end
