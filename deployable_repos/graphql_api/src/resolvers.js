// In-memory data store
let books = [
  { id: "1", title: "The Great Gatsby", author: "F. Scott Fitzgerald" },
  { id: "2", title: "To Kill a Mockingbird", author: "Harper Lee" },
  { id: "3", title: "1984", author: "George Orwell" },
];

let nextId = 4;

export const resolvers = {
  Query: {
    books: () => books,
    book: (_, { id }) => books.find((book) => book.id === id) || null,
  },
  Mutation: {
    addBook: (_, { title, author }) => {
      const newBook = {
        id: String(nextId++),
        title,
        author,
      };
      books.push(newBook);
      return newBook;
    },
  },
};
