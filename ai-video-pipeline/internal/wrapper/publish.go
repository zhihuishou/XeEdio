package wrapper

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

// Publisher defines the publishing interface, different platforms implement this interface.
type Publisher interface {
	// Publish uploads a video to the target platform and returns the publish URL.
	Publish(ctx context.Context, videoPath string, title string) (publishURL string, err error)
}

// XiaohongshuPublisher implements Publisher by calling the Xiaohongshu platform API.
type XiaohongshuPublisher struct {
	apiEndpoint string
	apiKey      string
	maxFileSize int64
	client      *http.Client
}

// NewXiaohongshuPublisher creates a new XiaohongshuPublisher with the given configuration.
func NewXiaohongshuPublisher(apiEndpoint, apiKey string, maxFileSize int64, timeout time.Duration) *XiaohongshuPublisher {
	return &XiaohongshuPublisher{
		apiEndpoint: strings.TrimRight(apiEndpoint, "/"),
		apiKey:      apiKey,
		maxFileSize: maxFileSize,
		client: &http.Client{
			Timeout: timeout,
		},
	}
}

// xiaohongshuUploadResponse represents the JSON response from the Xiaohongshu upload API.
type xiaohongshuUploadResponse struct {
	PublishURL string `json:"publish_url"`
	Error      string `json:"error,omitempty"`
}

// Publish validates the video file, uploads it to the Xiaohongshu platform API,
// and returns the publish URL on success.
//
// Validation steps:
//  1. Check that the video file exists (using ValidateFilesExist from render.go)
//  2. Check that the file size does not exceed maxFileSize
//
// The upload is performed via HTTP POST to {apiEndpoint}/v1/videos/upload with
// the video file as the request body and title + API key in headers.
func (p *XiaohongshuPublisher) Publish(ctx context.Context, videoPath string, title string) (string, error) {
	// Validate video file exists
	if missing := ValidateFilesExist([]string{videoPath}); len(missing) > 0 {
		return "", fmt.Errorf("publish_wrapper: video file does not exist: %s", videoPath)
	}

	// Check file size
	fileInfo, err := os.Stat(videoPath)
	if err != nil {
		return "", fmt.Errorf("publish_wrapper: failed to stat video file: %w", err)
	}
	if fileInfo.Size() > p.maxFileSize {
		return "", fmt.Errorf("publish_wrapper: file size %d bytes exceeds maximum allowed %d bytes", fileInfo.Size(), p.maxFileSize)
	}

	// Open the video file for upload
	file, err := os.Open(videoPath)
	if err != nil {
		return "", fmt.Errorf("publish_wrapper: failed to open video file: %w", err)
	}
	defer file.Close()

	// Build the upload request
	url := p.apiEndpoint + "/v1/videos/upload"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, file)
	if err != nil {
		return "", fmt.Errorf("publish_wrapper: failed to create upload request: %w", err)
	}

	req.Header.Set("Content-Type", "application/octet-stream")
	req.Header.Set("Authorization", "Bearer "+p.apiKey)
	req.Header.Set("X-Title", title)
	req.ContentLength = fileInfo.Size()

	// Execute the upload
	resp, err := p.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("publish_wrapper: upload request failed: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("publish_wrapper: failed to read upload response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("publish_wrapper: API returned status %d: %s", resp.StatusCode, string(respBody))
	}

	// Parse the response
	var uploadResp xiaohongshuUploadResponse
	if err := json.Unmarshal(respBody, &uploadResp); err != nil {
		return "", fmt.Errorf("publish_wrapper: failed to parse upload response: %w", err)
	}

	if uploadResp.Error != "" {
		return "", fmt.Errorf("publish_wrapper: API error: %s", uploadResp.Error)
	}

	if uploadResp.PublishURL == "" {
		return "", fmt.Errorf("publish_wrapper: API returned empty publish URL")
	}

	return uploadResp.PublishURL, nil
}
