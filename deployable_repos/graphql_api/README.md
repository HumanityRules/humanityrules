# GraphQL API

A minimal GraphQL API built with Apollo Server 4 and Express. Designed to test deployment agent capabilities with modern GraphQL patterns.

## Features

- GraphQL schema with Book type
- Query resolvers (books, book)
- Mutation resolver (addBook)
- Health check endpoint at `/health`
- In-memory data store

## Prerequisites

- Node.js 20 or higher

## Local Setup

1. Install dependencies:
   ```bash
   npm install
   ```

2. Copy environment file:
   ```bash
   cp .env.example .env
   ```

3. Start the server:
   ```bash
   npm start
   ```

The server runs on port **4000** by default.

## Endpoints

- **GraphQL Playground:** http://localhost:4000/graphql
- **Health Check:** http://localhost:4000/health

## Example Queries

Get all books:
```graphql
query {
  books {
    id
    title
    author
  }
}
```

Get a single book:
```graphql
query {
  book(id: "1") {
    id
    title
    author
  }
}
```

Add a book:
```graphql
mutation {
  addBook(title: "New Book", author: "Author Name") {
    id
    title
    author
  }
}
```

## Environment Variables

- **PORT** - Server port (default: 4000)
- **NODE_ENV** - Environment mode (development/production)
