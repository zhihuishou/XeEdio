package wrapper

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestXiaohongshuPublisher_Publish_Success(t *testing.T) {
	// Create a temporary video file
	tmpDir := t.TempDir()
	videoPath := filepath.Join(tmpDir, "test_video.mp4")
	if err := os.WriteFile(videoPath, []byte("fake video content"), 0o644); err != nil {
		t.Fatalf("failed to create temp video file: %v", err)
	}

	// Set up a mock HTTP server
	expectedURL := "https://www.xiaohongshu.com/video/12345"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Verify request
		if r.Method != http.MethodPost {
			t.Errorf("expected POST, got %s", r.Method)
		}
		if r.URL.Path != "/v1/videos/upload" {
			t.Errorf("expected /v1/videos/upload, got %s", r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer test-api-key" {
			t.Errorf("unexpected Authorization header: %s", r.Header.Get("Authorization"))
		}
		if r.Header.Get("X-Title") != "Test Video Title" {
			t.Errorf("unexpected X-Title header: %s", r.Header.Get("X-Title"))
		}

		resp := xiaohongshuUploadResponse{PublishURL: expectedURL}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	publisher := NewXiaohongshuPublisher(server.URL, "test-api-key", 1024*1024, 10*time.Second)

	publishURL, err := publisher.Publish(context.Background(), videoPath, "Test Video Title")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if publishURL != expectedURL {
		t.Errorf("expected publish URL %q, got %q", expectedURL, publishURL)
	}
}

func TestXiaohongshuPublisher_Publish_FileNotExist(t *testing.T) {
	publisher := NewXiaohongshuPublisher("http://localhost", "key", 1024*1024, 10*time.Second)

	_, err := publisher.Publish(context.Background(), "/nonexistent/video.mp4", "title")
	if err == nil {
		t.Fatal("expected error for nonexistent file, got nil")
	}
	if got := err.Error(); !contains(got, "does not exist") {
		t.Errorf("expected error to mention file does not exist, got: %s", got)
	}
}

func TestXiaohongshuPublisher_Publish_FileTooLarge(t *testing.T) {
	tmpDir := t.TempDir()
	videoPath := filepath.Join(tmpDir, "large_video.mp4")
	// Create a file with 100 bytes
	if err := os.WriteFile(videoPath, make([]byte, 100), 0o644); err != nil {
		t.Fatalf("failed to create temp video file: %v", err)
	}

	// Set max file size to 50 bytes
	publisher := NewXiaohongshuPublisher("http://localhost", "key", 50, 10*time.Second)

	_, err := publisher.Publish(context.Background(), videoPath, "title")
	if err == nil {
		t.Fatal("expected error for oversized file, got nil")
	}
	if got := err.Error(); !contains(got, "exceeds maximum") {
		t.Errorf("expected error to mention exceeds maximum, got: %s", got)
	}
}

func TestXiaohongshuPublisher_Publish_APIError(t *testing.T) {
	tmpDir := t.TempDir()
	videoPath := filepath.Join(tmpDir, "test_video.mp4")
	if err := os.WriteFile(videoPath, []byte("fake video"), 0o644); err != nil {
		t.Fatalf("failed to create temp video file: %v", err)
	}

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte("internal server error"))
	}))
	defer server.Close()

	publisher := NewXiaohongshuPublisher(server.URL, "key", 1024*1024, 10*time.Second)

	_, err := publisher.Publish(context.Background(), videoPath, "title")
	if err == nil {
		t.Fatal("expected error for API failure, got nil")
	}
	if got := err.Error(); !contains(got, "status 500") {
		t.Errorf("expected error to mention status 500, got: %s", got)
	}
}

func TestXiaohongshuPublisher_Publish_APIReturnsError(t *testing.T) {
	tmpDir := t.TempDir()
	videoPath := filepath.Join(tmpDir, "test_video.mp4")
	if err := os.WriteFile(videoPath, []byte("fake video"), 0o644); err != nil {
		t.Fatalf("failed to create temp video file: %v", err)
	}

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		resp := xiaohongshuUploadResponse{Error: "upload quota exceeded"}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	publisher := NewXiaohongshuPublisher(server.URL, "key", 1024*1024, 10*time.Second)

	_, err := publisher.Publish(context.Background(), videoPath, "title")
	if err == nil {
		t.Fatal("expected error for API error response, got nil")
	}
	if got := err.Error(); !contains(got, "upload quota exceeded") {
		t.Errorf("expected error to mention upload quota exceeded, got: %s", got)
	}
}

func TestXiaohongshuPublisher_Publish_ContextCancellation(t *testing.T) {
	tmpDir := t.TempDir()
	videoPath := filepath.Join(tmpDir, "test_video.mp4")
	if err := os.WriteFile(videoPath, []byte("fake video"), 0o644); err != nil {
		t.Fatalf("failed to create temp video file: %v", err)
	}

	// Server that blocks until context is cancelled
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		<-r.Context().Done()
	}))
	defer server.Close()

	publisher := NewXiaohongshuPublisher(server.URL, "key", 1024*1024, 10*time.Second)

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // Cancel immediately

	_, err := publisher.Publish(ctx, videoPath, "title")
	if err == nil {
		t.Fatal("expected error for cancelled context, got nil")
	}
}

// contains helper is defined in llm_test.go within the same package.
