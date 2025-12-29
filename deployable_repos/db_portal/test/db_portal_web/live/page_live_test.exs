defmodule DbPortalWeb.PageLiveTest do
  use DbPortalWeb.ConnCase

  import Phoenix.LiveViewTest

  test "disconnected and connected render", %{conn: conn} do
    {:ok, page_live, disconnected_html} = live(conn, "/load")
    assert disconnected_html =~ "By Bundle"
    assert render(page_live) =~ "By Bundle"
  end
end
