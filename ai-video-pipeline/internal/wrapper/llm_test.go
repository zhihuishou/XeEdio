package wrapper

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestDeepSeekLLMClient_Generate_Success(t *testing.T) {
	expectedContent := "Hello from DeepSeek"

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Verify request method and headers
		if r.Method != http.MethodPost {
			t.Errorf("expected POST, got %s", r.Method)
		}
		if r.Header.Get("Content-Type") != "application/json" {
			t.Errorf("expected Content-Type application/json, got %s", r.Header.Get("Content-Type"))
		}
		if r.Header.Get("Authorization") != "Bearer test-key" {
			t.Errorf("expected Authorization Bearer test-key, got %s", r.Header.Get("Authorization"))
		}
		if r.URL.Path != "/v1/chat/completions" {
			t.Errorf("expected path /v1/chat/completions, got %s", r.URL.Path)
		}

		// Verify request body
		var req chatRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Fatalf("failed to decode request body: %v", err)
		}
		if len(req.Messages) != 2 {
			t.Fatalf("expected 2 messages, got %d", len(req.Messages))
		}
		if req.Messages[0].Role != "system" || req.Messages[0].Content != "You are helpful" {
			t.Errorf("unexpected system message: %+v", req.Messages[0])
		}
		if req.Messages[1].Role != "user" || req.Messages[1].Content != "Say hello" {
			t.Errorf("unexpected user message: %+v", req.Messages[1])
		}

		resp := chatResponse{
			Choices: []chatChoice{
				{Message: chatMessage{Role: "assistant", Content: expectedContent}},
			},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	client := NewDeepSeekLLMClient("test-key", server.URL, 5*time.Second)
	result, err := client.Generate(context.Background(), "You are helpful", "Say hello")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result != expectedContent {
		t.Errorf("expected %q, got %q", expectedContent, result)
	}
}

func TestDeepSeekLLMClient_Generate_NonOKStatus(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		w.Write([]byte(`{"error":"invalid api key"}`))
	}))
	defer server.Close()

	client := NewDeepSeekLLMClient("bad-key", server.URL, 5*time.Second)
	_, err := client.Generate(context.Background(), "system", "user")
	if err == nil {
		t.Fatal("expected error for non-200 status")
	}
	if got := err.Error(); !contains(got, "status 401") {
		t.Errorf("expected error to mention status 401, got: %s", got)
	}
}

func TestDeepSeekLLMClient_Generate_EmptyChoices(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		resp := chatResponse{Choices: []chatChoice{}}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	client := NewDeepSeekLLMClient("test-key", server.URL, 5*time.Second)
	_, err := client.Generate(context.Background(), "system", "user")
	if err == nil {
		t.Fatal("expected error for empty choices")
	}
	if got := err.Error(); !contains(got, "empty choices") {
		t.Errorf("expected error to mention empty choices, got: %s", got)
	}
}

func TestDeepSeekLLMClient_Generate_ContextCancellation(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Simulate slow response — the context should cancel before this completes
		<-r.Context().Done()
	}))
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately

	client := NewDeepSeekLLMClient("test-key", server.URL, 5*time.Second)
	_, err := client.Generate(ctx, "system", "user")
	if err == nil {
		t.Fatal("expected error for cancelled context")
	}
}

func TestDeepSeekLLMClient_Generate_InvalidJSON(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`not valid json`))
	}))
	defer server.Close()

	client := NewDeepSeekLLMClient("test-key", server.URL, 5*time.Second)
	_, err := client.Generate(context.Background(), "system", "user")
	if err == nil {
		t.Fatal("expected error for invalid JSON response")
	}
	if got := err.Error(); !contains(got, "unmarshal") {
		t.Errorf("expected error to mention unmarshal, got: %s", got)
	}
}

// LLMClient interface compliance check
var _ LLMClient = (*DeepSeekLLMClient)(nil)

func contains(s, substr string) bool {
	return len(s) >= len(substr) && searchSubstring(s, substr)
}

func searchSubstring(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
