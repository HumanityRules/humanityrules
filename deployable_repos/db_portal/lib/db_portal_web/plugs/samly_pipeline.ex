defmodule DbPortalWeb.Plugs.SamlyPipeline do
  use Plug.Builder
  alias Samly.{Assertion}
  alias Plug.Conn

  plug :compute_attributes
  plug :jit_provision_user

  def compute_attributes(conn, _opts) do
    assertion = conn.private[:samly_assertion]
#    IO.inspect conn, label: "the conn"
#    IO.inspect assertion, label: "the assertion"

#    first_name = Map.get(assertion.attributes, "first_name")
#    last_name = Map.get(assertion.attributes, "last_name")
#
#    computed = %{
#      "full_name" => "#{first_name} #{last_name}",
#      "name" => "#{assertion.subject.name}",
#      "name_qualifier" => "#{assertion.subject.name_qualifier}",
#      "sp_name_qualifier" => "#{assertion.subject.sp_name_qualifier}",
#      "name_format" => "#{assertion.subject.name_format}",
#      "notonorafter" => "#{assertion.subject.notonorafter}",
#      "in_response_to" => "#{assertion.subject.in_response_to}"
#    }
#
#    assertion = %Assertion{assertion | computed: computed}

    conn
    |> put_private(:samly_assertion, assertion)

    # |>  send_resp(404, "attribute mapping failed")
    # |>  halt()
  end

  def jit_provision_user(conn, _opts) do
    IO.inspect Conn.get_session(conn, "samly_assertion_key"), label: "got samly key?"
    assertion = conn.private[:samly_assertion]

    with :ok <- verify_session_index(assertion),
         :ok <- verify_validity_period(assertion),
         %Assertion{subject: %Samly.Subject{name: name}, idp_id: _idp_id} <- assertion do
      #assertion_key = {idp_id, name}
      enable_user_session(conn, name)
    else
      _ -> conn
          |>  Conn.put_status(:unauthorized)
          #|>  Conn.halt()
    end

  end

  @hour_in_seconds 60*60
  #@minute_in_seconds 60
  @session_duration_secs 4*@hour_in_seconds
  def enable_user_session(conn, name) do
    IO.puts "********** enabling user name=#{name} **********"
    valid_until = DateTime.utc_now() |> DateTime.add(@session_duration_secs, :second)

    role = conn.private[:samly_assertion]
           |> Samly.get_attribute("Group")
    Conn.put_session(conn, :user_auth, {name, valid_until, role})
  end

  def verify_session_index(%Assertion{authn: authn, subject: subject } = _assertion) do
    original_id = subject.in_response_to
    returned_id = Map.get(authn, "session_index", nil)
    IO.puts "********** verify_session_index original=#{original_id} returned=#{returned_id}**********"

    if original_id == returned_id || original_id == "", do: :ok, else: :error
  end

  def verify_validity_period(%Assertion{authn: _authn, conditions: conditions } = assertion) do
    IO.inspect assertion, label: "assertion in verify validity period"
    {:ok, instant, 0} = Map.get(assertion, :issue_instant, nil)|> IO.inspect
              |> DateTime.from_iso8601
    {:ok, valid_start, 0} = Map.get(conditions, "not_before", nil)
                  |> DateTime.from_iso8601
    {:ok, valid_end, 0} = Map.get(conditions, "not_on_or_after", nil)
                |> DateTime.from_iso8601
    now = DateTime.utc_now()

    IO.puts "********** verify_validity_period instant=#{instant} start=#{valid_start} end=#{valid_end} now=#{now}**********"

    cond do
      DateTime.compare(valid_start, instant) == :gt -> :error
      DateTime.compare(instant, valid_end) != :lt -> :error

      DateTime.compare(valid_start, now) == :gt -> :error
      DateTime.compare(now, valid_end) != :lt -> :error
      true -> :ok
    end
  end
end

