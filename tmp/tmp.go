package main

import "fmt"

func main() {
    // 1. 创建一个初始为空的字符串切片
    // var inventory []string

    // 或者，在创建时直接塞入几个初始值（更常用）
    games := []string{"塞尔达", "黑神话"}
    fmt.Println("我拥有的游戏:", games)
}