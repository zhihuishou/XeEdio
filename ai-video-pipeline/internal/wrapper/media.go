package wrapper

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"time"
)

// MediaClient defines the interface for media search and download, enabling easy mocking in tests.
type MediaClient interface {
	// Search searches for a video matching the description, downloads it,
	// and returns the absolute file path of the downloaded video.
	Search(ctx context.Context, sceneID int, description string) (string, error)
}

// PexelsMediaClient implements MediaClient by calling the Pexels Video Search API.
type PexelsMediaClient struct {
	apiKey    string
	outputDir string
	client    *http.Client
}

// NewPexelsMediaClient creates a new PexelsMediaClient with the given API key, output directory, and timeout.
func NewPexelsMediaClient(apiKey, outputDir string, timeout time.Duration) *PexelsMediaClient {
	return &PexelsMediaClient{
		apiKey:    apiKey,
		outputDir: outputDir,
		client: &http.Client{
			Timeout: timeout,
		},
	}
}

// pexelsVideoSearchResponse represents the JSON response from the Pexels Video Search API.
type pexelsVideoSearchResponse struct {
	Videos []pexelsVideo `json:"videos"`
}

// pexelsVideo represents a single video in the Pexels API response.
type pexelsVideo struct {
	VideoFiles []pexelsVideoFile `json:"video_files"`
}

// pexelsVideoFile represents a single video file variant in the Pexels API response.
type pexelsVideoFile struct {
	Link    string `json:"link"`
	Quality string `json:"quality"`
	Width   int    `json:"width"`
	Height  int    `json:"height"`
}

// Search searches for a Pexels video matching the description, downloads the smallest/SD quality
// video file, and returns the absolute path of the downloaded file.
func (m *PexelsMediaClient) Search(ctx context.Context, sceneID int, description string) (string, error) {
	// Create output directory if it doesn't exist
	if err := os.MkdirAll(m.outputDir, 0o755); err != nil {
		return "", fmt.Errorf("media_wrapper: failed to create output directory: %w", err)
	}

	// Call Pexels Video Search API
	videoURL, err := m.searchVideoURL(ctx, description)
	if err != nil {
		return "", err
	}

	// Download the video file
	outputPath := filepath.Join(m.outputDir, fmt.Sprintf("scene_%d.mp4", sceneID))
	absPath, err := filepath.Abs(outputPath)
	if err != nil {
		return "", fmt.Errorf("media_wrapper: failed to resolve absolute path: %w", err)
	}

	if err := m.downloadFile(ctx, videoURL, absPath); err != nil {
		return "", err
	}

	return absPath, nil
}

// searchVideoURL calls the Pexels Video Search API and returns the URL of the smallest video file.
func (m *PexelsMediaClient) searchVideoURL(ctx context.Context, description string) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, "https://api.pexels.com/videos/search", nil)
	if err != nil {
		return "", fmt.Errorf("media_wrapper: failed to create search request: %w", err)
	}

	q := req.URL.Query()
	q.Set("query", description)
	q.Set("per_page", "1")
	req.URL.RawQuery = q.Encode()

	req.Header.Set("Authorization", m.apiKey)

	resp, err := m.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("media_wrapper: search request failed: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("media_wrapper: failed to read search response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("media_wrapper: Pexels API returned status %d: %s", resp.StatusCode, string(body))
	}

	var searchResp pexelsVideoSearchResponse
	if err := json.Unmarshal(body, &searchResp); err != nil {
		return "", fmt.Errorf("media_wrapper: failed to parse search response: %w", err)
	}

	if len(searchResp.Videos) == 0 {
		return "", fmt.Errorf("media_wrapper: no videos found for query %q", description)
	}

	videoFiles := searchResp.Videos[0].VideoFiles
	if len(videoFiles) == 0 {
		return "", fmt.Errorf("media_wrapper: video has no file variants for query %q", description)
	}

	// Pick the smallest/SD quality file: prefer "sd" quality, otherwise pick the smallest resolution
	fileURL := pickSmallestVideoFile(videoFiles)
	if fileURL == "" {
		return "", fmt.Errorf("media_wrapper: no valid video file URL found for query %q", description)
	}

	return fileURL, nil
}

// pickSmallestVideoFile selects the SD quality video file, or the smallest resolution file if no SD is available.
func pickSmallestVideoFile(files []pexelsVideoFile) string {
	// First, try to find an "sd" quality file
	for _, f := range files {
		if f.Quality == "sd" && f.Link != "" {
			return f.Link
		}
	}

	// Fallback: pick the file with the smallest resolution (width * height)
	var bestLink string
	bestPixels := int(^uint(0) >> 1) // max int
	for _, f := range files {
		if f.Link == "" {
			continue
		}
		pixels := f.Width * f.Height
		if pixels < bestPixels {
			bestPixels = pixels
			bestLink = f.Link
		}
	}
	return bestLink
}

// downloadFile downloads a file from the given URL and saves it to the destination path.
func (m *PexelsMediaClient) downloadFile(ctx context.Context, url, destPath string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return fmt.Errorf("media_wrapper: failed to create download request: %w", err)
	}

	resp, err := m.client.Do(req)
	if err != nil {
		return fmt.Errorf("media_wrapper: download request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("media_wrapper: download returned status %d", resp.StatusCode)
	}

	outFile, err := os.Create(destPath)
	if err != nil {
		return fmt.Errorf("media_wrapper: failed to create output file: %w", err)
	}
	defer outFile.Close()

	if _, err := io.Copy(outFile, resp.Body); err != nil {
		return fmt.Errorf("media_wrapper: failed to write video file: %w", err)
	}

	return nil
}
