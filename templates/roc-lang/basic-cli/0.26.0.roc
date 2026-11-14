app [main!] {{dependencies}}

import {{pf}}.Stdout

main! = |_args| {
	Stdout.line!("Hello, World!")?
	Ok({})
}
