defmodule PhoenixAppWeb.PostController do
  use Phoenix.Controller, formats: [:json]

  alias PhoenixApp.Post
  alias PhoenixApp.Repo

  import Ecto.Query

  def index(conn, _params) do
    posts = Repo.all(from p in Post, order_by: [desc: p.inserted_at])
    json(conn, %{data: Enum.map(posts, &post_json/1)})
  end

  def show(conn, %{"id" => id}) do
    case Repo.get(Post, id) do
      nil ->
        conn
        |> put_status(:not_found)
        |> json(%{error: "Post not found"})

      post ->
        json(conn, %{data: post_json(post)})
    end
  end

  def create(conn, %{"post" => post_params}) do
    changeset = Post.changeset(%Post{}, post_params)

    case Repo.insert(changeset) do
      {:ok, post} ->
        conn
        |> put_status(:created)
        |> json(%{data: post_json(post)})

      {:error, changeset} ->
        conn
        |> put_status(:unprocessable_entity)
        |> json(%{errors: format_errors(changeset)})
    end
  end

  def update(conn, %{"id" => id, "post" => post_params}) do
    case Repo.get(Post, id) do
      nil ->
        conn
        |> put_status(:not_found)
        |> json(%{error: "Post not found"})

      post ->
        changeset = Post.changeset(post, post_params)

        case Repo.update(changeset) do
          {:ok, updated_post} ->
            json(conn, %{data: post_json(updated_post)})

          {:error, changeset} ->
            conn
            |> put_status(:unprocessable_entity)
            |> json(%{errors: format_errors(changeset)})
        end
    end
  end

  def delete(conn, %{"id" => id}) do
    case Repo.get(Post, id) do
      nil ->
        conn
        |> put_status(:not_found)
        |> json(%{error: "Post not found"})

      post ->
        Repo.delete(post)
        send_resp(conn, :no_content, "")
    end
  end

  defp post_json(post) do
    %{
      id: post.id,
      title: post.title,
      body: post.body,
      inserted_at: post.inserted_at,
      updated_at: post.updated_at
    }
  end

  defp format_errors(changeset) do
    Ecto.Changeset.traverse_errors(changeset, fn {msg, opts} ->
      Regex.replace(~r"%{(\w+)}", msg, fn _, key ->
        opts |> Keyword.get(String.to_existing_atom(key), key) |> to_string()
      end)
    end)
  end
end
