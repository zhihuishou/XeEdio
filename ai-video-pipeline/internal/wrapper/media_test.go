package wrapper

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// MediaClient interface compliance check
var _ MediaClient = (*PexelsMediaClient)(nil)

func TestNewPexelsMediaClient(t *testing.T) {
	client := NewPexelsMediaClient("test-api-key", "/tmp/media-output", 30*time.Second)
	if client.apiKey != "test-api-key" {
		t.Errorf("expected apiKey %q, got %q", "test-api-key", client.apiKey)
	}
	if client.outputDir != "/tmp/media-output" {
		t.Errorf("expected outputDir %q, got %q", "/tmp/media-output", client.outputDir)
	}
	if client.client == nil {
		t.Error("expected non-nil http.Client")
	}
}

func TestPexelsMediaClient_Search_Success(t *testing.T) {
	// Set up a fake video file server
	videoServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "video/mp4")
		w.Write([]byte("fake-video-content"))
	}))
	defer videoServer.Close()

	// Set up a fake Pexels API server
	apiServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Verify authorization header
		if r.Header.Get("Authorization") != "test-key" {
			t.Errorf("expected Authorization header 'test-key', got %q", r.Header.Get("Authorization"))
		}
		// Verify query params
		if r.URL.Query().Get("query") != "sunset" {
			t.Errorf("expected query 'sunset', got %q", r.URL.Query().Get("query"))
		}
		if r.URL.Query().Get("per_page") != "1" {
			t.Errorf("expected per_page '1', got %q", r.URL.Query().Get("per_page"))
		}

		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{
			"videos": [{
				"video_files": [
					{"link": "%s/video.mp4", "quality": "hd", "width": 1920, "height": 1080},
					{"link": "%s/video_sd.mp4", "quality": "sd", "width": 640, "height": 480}
				]
			}]
		}`, videoServer.URL, videoServer.URL)
	}))
	defer apiServer.Close()

	tmpDir := t.TempDir()

	// Create client that points to our fake API server
	client := NewPexelsMediaClient("test-key", tmpDir, 10*time.Second)
	// Override the search URL by using a custom client that rewrites the host
	client.client = apiServer.Client()

	// We need to intercept the search call to use our test server.
	// Instead, let's test via the internal methods by creating a wrapper that uses the test server.
	// For a proper test, we'll create a helper that overrides the Pexels API URL.
	// Since the URL is hardcoded, we'll test using a different approach: test the full Search method
	// by creating a PexelsMediaClient with a custom http.Client that redirects requests.

	transport := &rewriteTransport{
		apiBase:   apiServer.URL,
		videoBase: videoServer.URL,
		rt:        http.DefaultTransport,
	}
	client.client = &http.Client{Transport: transport}

	result, err := client.Search(context.Background(), 1, "sunset")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Verify the path is absolute
	if !filepath.IsAbs(result) {
		t.Errorf("expected absolute path, got %q", result)
	}

	// Verify filename pattern
	if filepath.Base(result) != "scene_1.mp4" {
		t.Errorf("expected filename 'scene_1.mp4', got %q", filepath.Base(result))
	}

	// Verify file was downloaded
	content, err := os.ReadFile(result)
	if err != nil {
		t.Fatalf("failed to read downloaded file: %v", err)
	}
	if string(content) != "fake-video-content" {
		t.Errorf("expected file content 'fake-video-content', got %q", string(content))
	}
}

// rewriteTransport intercepts HTTP requests and redirects Pexels API calls to test servers.
type rewriteTransport struct {
	apiBase   string
	videoBase string
	rt        http.RoundTripper
}

func (t *rewriteTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if strings.Contains(req.URL.Host, "api.pexels.com") {
		// Rewrite to test API server
		req.URL.Scheme = "http"
		req.URL.Host = strings.TrimPrefix(t.apiBase, "http://")
	}
	return t.rt.RoundTrip(req)
}

func TestPexelsMediaClient_Search_CreatesOutputDir(t *testing.T) {
	videoServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("video"))
	}))
	defer videoServer.Close()

	apiServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"videos":[{"video_files":[{"link":"%s/v.mp4","quality":"sd","width":640,"height":480}]}]}`, videoServer.URL)
	}))
	defer apiServer.Close()

	tmpDir := t.TempDir()
	nestedDir := filepath.Join(tmpDir, "nested", "media-output")

	client := NewPexelsMediaClient("key", nestedDir, 10*time.Second)
	client.client = &http.Client{Transport: &rewriteTransport{
		apiBase: apiServer.URL, videoBase: videoServer.URL, rt: http.DefaultTransport,
	}}

	_, err := client.Search(context.Background(), 1, "test")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	info, err := os.Stat(nestedDir)
	if err != nil {
		t.Fatalf("expected output directory to be created: %v", err)
	}
	if !info.IsDir() {
		t.Fatal("expected output path to be a directory")
	}
}

func TestPexelsMediaClient_Search_NoResults(t *testing.T) {
	apiServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"videos":[]}`))
	}))
	defer apiServer.Close()

	tmpDir := t.TempDir()
	client := NewPexelsMediaClient("key", tmpDir, 10*time.Second)
	client.client = &http.Client{Transport: &rewriteTransport{
		apiBase: apiServer.URL, rt: http.DefaultTransport,
	}}

	_, err := client.Search(context.Background(), 1, "nonexistent")
	if err == nil {
		t.Fatal("expected error for empty results")
	}
	if !strings.Contains(err.Error(), "no videos found") {
		t.Errorf("expected 'no videos found' in error, got: %s", err.Error())
	}
}

func TestPexelsMediaClient_Search_APIError(t *testing.T) {
	apiServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		w.Write([]byte(`{"error":"Invalid API key"}`))
	}))
	defer apiServer.Close()

	tmpDir := t.TempDir()
	client := NewPexelsMediaClient("bad-key", tmpDir, 10*time.Second)
	client.client = &http.Client{Transport: &rewriteTransport{
		apiBase: apiServer.URL, rt: http.DefaultTransport,
	}}

	_, err := client.Search(context.Background(), 1, "test")
	if err == nil {
		t.Fatal("expected error for API error response")
	}
	if !strings.Contains(err.Error(), "status 401") {
		t.Errorf("expected 'status 401' in error, got: %s", err.Error())
	}
}

func TestPexelsMediaClient_Search_ContextCancellation(t *testing.T) {
	apiServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"videos":[{"video_files":[{"link":"http://example.com/v.mp4","quality":"sd","width":640,"height":480}]}]}`))
	}))
	defer apiServer.Close()

	tmpDir := t.TempDir()
	client := NewPexelsMediaClient("key", tmpDir, 10*time.Second)

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately

	_, err := client.Search(ctx, 1, "test")
	if err == nil {
		t.Fatal("expected error for cancelled context")
	}
}

func TestPexelsMediaClient_Search_PrefersSDQuality(t *testing.T) {
	// Track which video URL was requested
	var downloadedURL string
	videoServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		downloadedURL = r.URL.Path
		w.Write([]byte("video"))
	}))
	defer videoServer.Close()

	apiServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"videos":[{"video_files":[
			{"link":"%s/hd.mp4","quality":"hd","width":1920,"height":1080},
			{"link":"%s/sd.mp4","quality":"sd","width":640,"height":480},
			{"link":"%s/uhd.mp4","quality":"uhd","width":3840,"height":2160}
		]}]}`, videoServer.URL, videoServer.URL, videoServer.URL)
	}))
	defer apiServer.Close()

	tmpDir := t.TempDir()
	client := NewPexelsMediaClient("key", tmpDir, 10*time.Second)
	client.client = &http.Client{Transport: &rewriteTransport{
		apiBase: apiServer.URL, videoBase: videoServer.URL, rt: http.DefaultTransport,
	}}

	_, err := client.Search(context.Background(), 1, "test")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if downloadedURL != "/sd.mp4" {
		t.Errorf("expected SD quality video to be downloaded (/sd.mp4), got %q", downloadedURL)
	}
}

func TestPickSmallestVideoFile(t *testing.T) {
	tests := []struct {
		name     string
		files    []pexelsVideoFile
		expected string
	}{
		{
			name: "prefers sd quality",
			files: []pexelsVideoFile{
				{Link: "http://example.com/hd.mp4", Quality: "hd", Width: 1920, Height: 1080},
				{Link: "http://example.com/sd.mp4", Quality: "sd", Width: 640, Height: 480},
			},
			expected: "http://example.com/sd.mp4",
		},
		{
			name: "falls back to smallest resolution",
			files: []pexelsVideoFile{
				{Link: "http://example.com/big.mp4", Quality: "hd", Width: 1920, Height: 1080},
				{Link: "http://example.com/small.mp4", Quality: "hd", Width: 320, Height: 240},
			},
			expected: "http://example.com/small.mp4",
		},
		{
			name: "skips empty links",
			files: []pexelsVideoFile{
				{Link: "", Quality: "sd", Width: 640, Height: 480},
				{Link: "http://example.com/valid.mp4", Quality: "hd", Width: 1920, Height: 1080},
			},
			expected: "http://example.com/valid.mp4",
		},
		{
			name:     "returns empty for no files",
			files:    []pexelsVideoFile{},
			expected: "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := pickSmallestVideoFile(tt.files)
			if result != tt.expected {
				t.Errorf("expected %q, got %q", tt.expected, result)
			}
		})
	}
}

func TestPexelsMediaClient_Search_DownloadFailure(t *testing.T) {
	videoServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer videoServer.Close()

	apiServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"videos":[{"video_files":[{"link":"%s/v.mp4","quality":"sd","width":640,"height":480}]}]}`, videoServer.URL)
	}))
	defer apiServer.Close()

	tmpDir := t.TempDir()
	client := NewPexelsMediaClient("key", tmpDir, 10*time.Second)
	client.client = &http.Client{Transport: &rewriteTransport{
		apiBase: apiServer.URL, videoBase: videoServer.URL, rt: http.DefaultTransport,
	}}

	_, err := client.Search(context.Background(), 1, "test")
	if err == nil {
		t.Fatal("expected error for download failure")
	}
	if !strings.Contains(err.Error(), "download returned status 500") {
		t.Errorf("expected download error message, got: %s", err.Error())
	}
}
