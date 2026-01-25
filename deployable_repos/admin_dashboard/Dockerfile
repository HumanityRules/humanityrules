FROM node:20-alpine

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install all dependencies (including devDependencies for build)
RUN npm ci

# Copy source code
COPY . .

# Build the React frontend
RUN npm run build

# Remove devDependencies to reduce image size
RUN npm prune --production

# Expose the port
EXPOSE 3000

# Set production environment
ENV NODE_ENV=production

# Start the server
CMD ["node", "server/index.js"]
