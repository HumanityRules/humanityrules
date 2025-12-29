defmodule DbPortalWeb.Avatar do
  @moduledoc "Started by ConCacheSupervisor.  Used to access avatars"

  require Logger

  def start_link(args) do
    ConCache.start_link args
  end


  def get(email) do
    case ConCache.get(:avatar_cache, email) do
      nil -> avatar = retrieve_avatar(email)
        ConCache.put(:avatar_cache, email, avatar)
        avatar

      avatar -> avatar
    end
  end

  def retrieve_avatar(email) do
    Logger.debug("retrieve_avatar: #{email}")
    with %{"ok"=>true}=r <- do_lookup_by_email(email) do
      url = Kernel.get_in(r, ~w(user profile image_32))

      if url, do: {:img, url}, else: {:email, email}
    else
      _ -> {:email, email}
    end
  end

  def do_lookup_by_email(email) do
    try do
      Slack.Web.Users.lookup_by_email(email)
    rescue
      e in HTTPoison.Error -> {:error,e}
    end
  end

end
