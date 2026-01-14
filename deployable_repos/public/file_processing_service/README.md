# Distributed Image Processor (DIP)

This document outlines the architecture and workflows of the **Distributed Image Processor**, a service designed to handle image uploads, preprocessing, and real-time processing. The system utilizes serverless components to ensure scalability and efficiency while providing users with quick feedback on their image processing requests.

### Service Workflows

---

<details open>
<summary>Real Time Processor (RTP)</summary>

#### Architecture Diagram

![File Upload and RTP Processing Flow](https://github.com/user-attachments/assets/bd1131ec-dd30-41b1-ac13-a8e0ecca7ca6)

#### 1. File Upload Flow

- The user sends a request to upload a file to the `rtp-request-files` bucket.
- Upon successful saving of the image, a public URL is returned to the user.

#### 2. RTP Flow

- The user sends a request to process the image using the `/process-file` endpoint.
- The API Gateway receives the request and puts it into the `rtp-request-topic`.
- The `rtp-request-topic` fans out the request to the `rtp-request-queue`.
- **`rtp-preprocessor-lambda`:**
  - Retrieves the request from `rtp-request-queue`.
  - Downloads the file stored in `rtp-request-files`.
  - Performs preprocessing on the image.
  - Stores the preprocessed image in `rtp-preprocessed-files`.
  - Records the request data in `rtp-data-store` (DynamoDB).
  - Upon successful preprocessing, sends a notification to the `rtp-preprocess-ok-topic`.
- The `rtp-preprocess-ok-topic` fans out the request to the `rtp-preprocess-ok-queue`.
- **`rtp-file-processor-lambda`:**
  - Retrieves the request from `rtp-preprocess-ok-queue`.
  - Downloads the preprocessed image from `rtp-preprocessed-files`.
  - Applies additional processing (e.g., applying filters).
  - Stores the final processed image in `rtp-processed-files`.
  - Updates the status in the `rtp-data-store` (DynamoDB).
  - Upon successful processing, sends a notification to the `rtp-notifier-topic`.
- The `rtp-notifier-topic` fans out to Amazon SES, which notifies the user of their processing results via email.

</details>

### Deployment Workflows

---

<details>

<summary>CI/CD Workflow</summary>

#### Workflow Diagram

![CI/CD Flow](https://github.com/user-attachments/assets/1c924a0a-4987-47d9-b6c2-ac7a61f25045)

</details>
