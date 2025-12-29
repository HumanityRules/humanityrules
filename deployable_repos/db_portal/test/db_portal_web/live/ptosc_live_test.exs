defmodule DbPortalWeb.PtoscLiveTest do
  use DbPortalWeb.ConnCase

  import Phoenix.LiveViewTest

  alias DbPortal.DbMetadata.{DbSchema, Cluster}
  alias DbPortal.Permission

  setup do
    now = NaiveDateTime.utc_now()

    {:ok, cluster} =
      Cluster.test_changeset(%Cluster{}, %{
        env: "p",
        read_endpoint: "r",
        write_endpoint: "127.0.0.1",
        name: "N"
      })
      |> DbPortal.Repo.insert_or_update()

    {:ok, schema} = DbSchema.changeset_from_name("foo", cluster.id)
                    |> DbPortal.Repo.insert_or_update

    tablename = ~s(test_ptosc_#{Date.utc_today() |> Calendar.strftime("%Y_%m_%d")})
    {:ok, db} = MyXQL.start_link(hostname: "127.0.0.1", username: "root", database: "foo")
    MyXQL.query(db, "create table #{tablename} (firstcol int NOT NULL PRIMARY KEY)")

    [
      schemas:
        1..5
        |> Enum.map(&DbSchema.changeset_from_name("Schema#{&1}", cluster.id))
        |> Enum.map(&DbPortal.Repo.insert_or_update/1)
        |> Enum.map(fn {:ok, schema} -> schema end),
      ptosc_schema: schema.id,
      tablename: tablename
    ]
  end

  # test "ptosc requires authentication", %{conn: conn} do
  #   result = get(conn, "/ptosc") # just the initial request, as cannot transition to liveview
  #   assert %Plug.Conn{status: 403} = result
  #
  #   # assert {:error,
  #   #         {:redirect,
  #   #          %{
  #   #            flash: %{"error" => "Not allowed access to Elixir.DbPortalWeb.PtoscLive"},
  #   #            to: "/login"
  #   #          }}} = result
  # end
  #
  # test "data engr is allowed", %{conn: conn} do
  #   conn = as_data_eng(conn)
  #
  #   {:ok, view, disconnected_html} = live(conn, "/ptosc")
  # end

  test "only show permitted schemas in dropdown", context  do
    %{conn: conn, schemas: schemas} = context
    with_page_permission("ptosc", [Enum.random(schemas).id])
    {:ok, _view, _disconnected_html} = live(conn, "/ptosc")
  end

  def with_page_permission(page, schema_ids) do
    perms =
      schema_ids
      |> Enum.map(
        &%{email: "dev@example.com", page: "ptosc", schema_id: &1, tickets: "TICK-#{&1}"}
      )
      |> Enum.map(&Permission.create_changeset(%Permission{}, &1))
      |> Enum.map(&DbPortal.Repo.insert_or_update/1)
  end

test "Valid input submitted brings to launch component", %{conn: conn, ptosc_schema: schema} do
    {:ok, lv, _html} = live(conn, ~p"/ptosc")
    send(lv.pid, {:updated_schema, %{schema: schema }})

    result =
      lv
      |> form("#new_job_form", %{
        "ptosc_request" => %{
          "ticket" => "TKT-123",
          "table" => "t4",
          "chunk_time" => "0.07",
          "alter" => "modify nothing",
          # hidden fields
          "host" => "127.0.0.1",
          "schema_name" => "foo",
          }
      })
      |> render_submit()

    IO.inspect(lv)
    assert_patch lv, ~p"/ptosc/launch"
    assert has_element?(lv, "#confirm_dry_run") # has the modal
  end

  test "dry run completes successfully", %{conn: conn, ptosc_schema: schema, tablename: tablename} do {:ok, lv, _html} = live(conn, ~p"/ptosc")
    send(lv.pid, {:updated_schema, %{schema: schema }})

    col_name = "col_#{:rand.uniform(10000)}"
    result =
      lv
      |> form("#new_job_form", %{
        "ptosc_request" => %{
          "ticket" => "TKT-123",
          "table" => tablename,
          "chunk_time" => "0.07",
          "alter" => "add column #{col_name} int",
          # hidden fields
          "host" => "127.0.0.1",
          "schema_name" => "foo",
          }
      })
      |> render_submit()

    assert_patch lv, "/ptosc/launch"

    lv
    |> element("#confirm_dry_run-confirm") # the modal
    |> render_click()

    start_ptosc_btn = lv
      |> element(~s(button[phx-click="start_ptosc"]))
    assert has_element?(start_ptosc_btn)

    not_disabled =  lv
      |> element(~s(button[phx-click="start_ptosc" disabled]))

    refute has_element?(not_disabled)

  end

  test "ptosc completes successfully", %{conn: conn, ptosc_schema: schema, tablename: tablename} do {:ok, lv, _html} = live(conn, ~p"/ptosc")
    send(lv.pid, {:updated_schema, %{schema: schema }})

    col_name = "col_#{:rand.uniform(10000)}"
    result =
      lv
      |> form("#new_job_form", %{
        "ptosc_request" => %{
          "ticket" => "TKT-123",
          "table" => tablename,
          "chunk_time" => "0.07",
          "alter" => "add column #{col_name} int",
          # hidden fields
          "host" => "127.0.0.1",
          "schema_name" => "foo",
          }
      })
      |> render_submit()

    assert_patch lv # must receive the msg from the patch so subsequent assert_patch() calls will get the right msg

    lv
    |> element("button#confirm_dry_run-confirm") # the modal
    |> render_click()

    Process.sleep(1000)
    start_ptosc_btn = lv
      |> element(~s(button[phx-click="start_ptosc"]))
      |> render_click()

    to = assert_patch( lv) |> IO.inspect(label: "patched to?")
    assert String.match?(to, ~r"/ptosc/job/[0-9]+"), "should patch to a particular job"

  end
end
