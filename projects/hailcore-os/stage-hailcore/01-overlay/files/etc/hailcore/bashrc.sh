# hailcore shell theme. Sourced from ~/.bashrc for interactive shells.
case $- in *i*) ;; *) return ;; esac

if [ "$(id -u)" -eq 0 ]; then _hc_u='\[\e[1;91m\]'; else _hc_u='\[\e[1;96m\]'; fi
_hc_f='\[\e[35m\]'   # frame
_hc_r='\[\e[0m\]'
_hc_status() {
    local s=$?
    [ $s -ne 0 ] && printf '\001\e[91m\002[%s]\001\e[35m\002─' "$s"
}
PS1="${_hc_f}┌─[${_hc_u}\u${_hc_f}@\[\e[95m\]\h${_hc_f}]─[\[\e[92m\]\w${_hc_f}]\n${_hc_f}└─\$(_hc_status)\[\e[1;93m\]>>${_hc_r} "
unset _hc_u _hc_f _hc_r

export LS_COLORS='di=1;96:ln=95:so=93:pi=93:ex=1;92:bd=93:cd=93:su=1;91:sg=1;91:tw=1;96:ow=1;96:*.gguf=95:*.hef=95:*.json=93:*.py=92:*.sh=92'
alias ls='ls --color=auto'
alias grep='grep --color=auto'
alias ll='ls -lah'
alias chat='hc chat'
alias temp='vcgencmd measure_temp'
