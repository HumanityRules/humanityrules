defmodule Debug do
  def live_list do
    Process.list()
    |> Enum.map(& {
      &1,
      Process.info(&1, [:dictionary])
      |> hd()
      |> elem(1)
      |> Keyword.get(:"$initial_call", {})
    })
    |> Enum.filter(fn {_, process} ->
      process != nil && process != {} &&
        elem(process, 0) == Phoenix.LiveView.Channel
    end)
    |> Enum.map(& elem(&1, 0))
  end

  def lv_from_email(email) do
    em_re = Regex.compile!(email)
    live_list()
    |> Enum.filter(fn p ->
      case assigns(p) do
        %{email: e} -> Regex.match?(em_re, e)
        _ -> false
      end
    end)
  end

  def state(pid) when is_pid(pid), do: :sys.get_state(pid)
  def view(pid) when is_pid(pid), do: state(pid).socket.view
  def assigns(pid) when is_pid(pid), do: state(pid).socket.assigns
  def email(pid) when is_pid(pid), do: state(pid).socket.assigns.email
end
