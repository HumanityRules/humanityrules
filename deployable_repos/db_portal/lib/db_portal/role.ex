defmodule DbPortal.Role do
  defp default_session(), do: {nil, nil, nil}

  def get_session(conn) do
    case Plug.Conn.get_session(conn, "user_auth") do
      {_email, _valid_until, _role} = session -> session
      nil -> default_session()
    end
  end

  @data_engrs_list ~w(dev@example.com rliebling@coursehero.com rliebling@gmail.com vmendiluce@coursehero.com aawasthi@coursehero.com rokun@coursehero.com sneisius@coursehero.com gmulgaonkar@coursehero.com ktiwana@courseher.com jpang@coursehero.com rliu@coursehero.com)
  def satisfies_need?(nil, _), do: true

  def satisfies_need?(:data_engr, {email, _valid_until, _role} = _user_auth) do
    Enum.member?(@data_engrs_list, email)
  end
end
